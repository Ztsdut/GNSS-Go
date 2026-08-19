from __future__ import annotations

import base64
import os
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse
from urllib.request import getproxies

import httpx

from gnssgo.exceptions import ConfigurationError

if TYPE_CHECKING:
    from gnssgo.config.settings import NetworkSettings


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    mode: str = "system"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    use_for_http: bool = True
    use_for_sftp: bool = True
    use_for_ftp: bool = True

    @classmethod
    def from_settings(
        cls,
        settings: "NetworkSettings | None",
        *,
        legacy_proxy: str | None = None,
    ) -> "ProxyConfig":
        if settings is None:
            if legacy_proxy:
                return cls.from_url(legacy_proxy)
            return cls(mode="system")
        mode = str(getattr(settings, "mode", "system") or "system").strip().lower()
        host = str(getattr(settings, "host", "") or "").strip()
        try:
            port = int(getattr(settings, "port", 0) or 0)
        except (TypeError, ValueError):
            port = 0
        proxy = cls(
            mode=mode,
            host=host,
            port=port,
            username=str(getattr(settings, "username", "") or ""),
            password=str(getattr(settings, "password", "") or ""),
            use_for_http=bool(getattr(settings, "use_for_http", True)),
            use_for_sftp=bool(getattr(settings, "use_for_sftp", True)),
            use_for_ftp=bool(getattr(settings, "use_for_ftp", True)),
        )
        # Migrate the old single URL field without breaking existing settings.json.
        old = str(getattr(settings, "proxy", "") or legacy_proxy or "").strip()
        if old and mode in {"", "system"} and not host:
            migrated = cls.from_url(old)
            return cls(
                mode=migrated.mode,
                host=migrated.host,
                port=migrated.port,
                username=migrated.username,
                password=migrated.password,
                use_for_http=proxy.use_for_http,
                use_for_sftp=proxy.use_for_sftp,
                use_for_ftp=proxy.use_for_ftp,
            )
        return proxy

    @classmethod
    def from_url(cls, value: str) -> "ProxyConfig":
        raw = value.strip()
        if "://" not in raw:
            raw = "http://" + raw
        parsed = urlparse(raw)
        scheme = parsed.scheme.lower()
        mode = "socks5" if scheme.startswith("socks") else "http"
        default_port = 1080 if mode == "socks5" else 8080
        return cls(
            mode=mode,
            host=parsed.hostname or "",
            port=parsed.port or default_port,
            username=parsed.username or "",
            password=parsed.password or "",
        )

    @property
    def custom(self) -> bool:
        return self.mode in {"http", "socks5"} and bool(self.host and self.port)

    def enabled_for(self, protocol: str) -> bool:
        protocol = protocol.lower()
        if self.mode == "direct":
            return False
        if protocol in {"http", "https"}:
            return self.use_for_http
        if protocol == "sftp":
            return self.use_for_sftp
        if protocol == "ftp":
            return self.use_for_ftp
        return False

    @staticmethod
    def _normalize_proxy_url(value: str, *, default_scheme: str = "http") -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        if "://" not in raw:
            raw = f"{default_scheme}://{raw}"
        return raw

    @staticmethod
    def _parse_windows_proxy_server(value: str) -> dict[str, str]:
        """Parse WinINet ProxyServer values.

        Windows accepts both a single proxy (``127.0.0.1:7890``) and
        per-protocol values such as
        ``http=127.0.0.1:7890;https=127.0.0.1:7890;socks=127.0.0.1:7891``.
        """
        raw = str(value or "").strip()
        if not raw:
            return {}
        if "=" not in raw:
            url = ProxyConfig._normalize_proxy_url(raw)
            return {"http": url, "https": url} if url else {}
        result: dict[str, str] = {}
        for item in raw.split(";"):
            if "=" not in item:
                continue
            key, val = item.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
            if not val:
                continue
            scheme = "socks5" if key.startswith("socks") else "http"
            url = ProxyConfig._normalize_proxy_url(val, default_scheme=scheme)
            if url:
                result[key] = url
        return result

    @staticmethod
    def _windows_registry_proxy_info() -> tuple[dict[str, str], str | None]:
        """Read the current user's WinINet proxy configuration.

        Returns ``(static_proxies, pac_url)``.  Importing winreg is guarded so
        this module remains portable on Linux/macOS.
        """
        if os.name != "nt":
            return {}, None
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                try:
                    enabled = bool(winreg.QueryValueEx(key, "ProxyEnable")[0])
                except OSError:
                    enabled = False
                try:
                    server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "")
                except OSError:
                    server = ""
                try:
                    pac_url = str(winreg.QueryValueEx(key, "AutoConfigURL")[0] or "").strip() or None
                except OSError:
                    pac_url = None
            proxies = ProxyConfig._parse_windows_proxy_server(server) if enabled else {}
            return proxies, pac_url
        except Exception:
            return {}, None

    def _system_proxy_candidates(self, protocol: str) -> list[tuple[str, str]]:
        """Return ordered proxy URLs plus their discovery source.

        Raw TCP protocols prefer SOCKS when available, then HTTPS/HTTP CONNECT.
        HTTP(S) prefers the matching protocol.  We intentionally inspect both
        Python's proxy discovery and the Windows WinINet registry because many
        desktop proxy applications only expose one of those views.
        """
        protocol = protocol.lower()
        discovered: list[tuple[str, str]] = []

        def add(source: str, value: str | None, default_scheme: str = "http") -> None:
            url = self._normalize_proxy_url(value or "", default_scheme=default_scheme)
            if not url:
                return
            if all(existing != url for existing, _ in discovered):
                discovered.append((url, source))

        # Explicit environment variables are useful even when urllib's platform
        # discovery is incomplete.
        env = {k.lower(): v for k, v in os.environ.items()}
        if protocol in {"sftp", "ftp"}:
            add("environment ALL_PROXY", env.get("all_proxy"), "socks5")
            add("environment HTTPS_PROXY", env.get("https_proxy"))
            add("environment HTTP_PROXY", env.get("http_proxy"))
        else:
            add(f"environment {protocol.upper()}_PROXY", env.get(f"{protocol}_proxy"))
            add("environment HTTPS_PROXY", env.get("https_proxy"))
            add("environment HTTP_PROXY", env.get("http_proxy"))
            add("environment ALL_PROXY", env.get("all_proxy"), "socks5")

        try:
            proxies = {str(k).lower(): str(v) for k, v in getproxies().items() if v}
        except Exception:
            proxies = {}
        if protocol in {"sftp", "ftp"}:
            add("urllib socks", proxies.get("socks"), "socks5")
            add("urllib https", proxies.get("https"))
            add("urllib http", proxies.get("http"))
        else:
            add(f"urllib {protocol}", proxies.get(protocol))
            add("urllib https", proxies.get("https"))
            add("urllib http", proxies.get("http"))

        registry, _pac = self._windows_registry_proxy_info()
        if protocol in {"sftp", "ftp"}:
            add("Windows registry socks", registry.get("socks") or registry.get("socks5"), "socks5")
            add("Windows registry https", registry.get("https"))
            add("Windows registry http", registry.get("http"))
        else:
            add(f"Windows registry {protocol}", registry.get(protocol))
            add("Windows registry https", registry.get("https"))
            add("Windows registry http", registry.get("http"))
        return discovered

    def _system_proxy_url(self, protocol: str = "https") -> str | None:
        candidates = self._system_proxy_candidates(protocol)
        return candidates[0][0] if candidates else None

    def system_proxy_diagnostic(self, protocol: str = "sftp") -> str:
        candidates = self._system_proxy_candidates(protocol)
        if candidates:
            url, source = candidates[0]
            parsed = urlparse(url)
            safe = f"{parsed.scheme}://{parsed.hostname or ''}:{parsed.port or ''}"
            return f"{safe} ({source})"
        _registry, pac = self._windows_registry_proxy_info()
        if pac:
            return f"PAC configured ({pac}), but no static HTTP/SOCKS proxy was resolved"
        return "none detected"

    def tunnel_proxy(self, protocol: str) -> "ProxyConfig | None":
        """Resolve the first proxy usable for a raw TCP tunnel such as SFTP."""
        if not self.enabled_for(protocol):
            return None
        if self.custom:
            return self
        if self.mode == "system":
            raw = self._system_proxy_url(protocol)
            if raw:
                return ProxyConfig.from_url(raw)
        return None

    def uses_tunnel_proxy(self, protocol: str) -> bool:
        return self.tunnel_proxy(protocol) is not None

    def validate(self) -> None:
        if self.mode not in {"direct", "system", "http", "socks5"}:
            raise ConfigurationError(f"Unsupported proxy mode: {self.mode}")
        if self.mode in {"http", "socks5"}:
            if not self.host:
                raise ConfigurationError("Proxy host is required.")
            if not (1 <= self.port <= 65535):
                raise ConfigurationError("Proxy port must be between 1 and 65535.")

    def proxy_url(self, *, for_curl: bool = False, protocol: str = "https") -> str | None:
        if not self.enabled_for(protocol):
            return None
        if self.mode == "system":
            return self._system_proxy_url(protocol)
        if not self.custom:
            return None
        if self.mode == "socks5":
            # curl's socks5h makes DNS resolution happen at the proxy, which is
            # particularly useful when the local network cannot resolve/reach the
            # destination directly. httpx accepts socks5://.
            scheme = "socks5h" if for_curl else "socks5"
        else:
            scheme = "http"
        auth = ""
        if self.username:
            auth = quote(self.username, safe="")
            if self.password:
                auth += ":" + quote(self.password, safe="")
            auth += "@"
        return f"{scheme}://{auth}{self.host}:{self.port}"

    def display_url(self) -> str:
        if self.mode in {"direct", "system"}:
            return self.mode
        scheme = "socks5" if self.mode == "socks5" else "http"
        user = f"{self.username}@" if self.username else ""
        return f"{scheme}://{user}{self.host}:{self.port}"

    def open_socket(
        self,
        target_host: str,
        target_port: int,
        timeout: float,
        *,
        protocol: str = "sftp",
    ) -> socket.socket:
        """Open a TCP connection directly or through the effective proxy.

        In ``system`` mode, GNSS Go now reuses the discovered Windows/system
        HTTP proxy as a CONNECT tunnel for SFTP when ``use_for_sftp`` is enabled.
        This is the important case for networks that block direct outbound :22.
        """
        self.validate()
        if self.mode == "system" and self.enabled_for(protocol):
            candidates = self._system_proxy_candidates(protocol)
            if candidates:
                errors: list[str] = []
                for raw, source in candidates:
                    proxy = ProxyConfig.from_url(raw)
                    try:
                        proxy.validate()
                        if proxy.mode == "socks5":
                            return proxy._open_socks5_socket(target_host, target_port, timeout)
                        return proxy._open_http_connect_socket(target_host, target_port, timeout)
                    except Exception as exc:
                        errors.append(f"{source}: {exc}")
                raise OSError("All detected system proxies failed: " + " | ".join(errors))
            # No static system proxy was discoverable.  Keep direct fallback for
            # compatibility, but the connectivity test labels this explicitly.
            return socket.create_connection((target_host, target_port), timeout=timeout)

        proxy = self.tunnel_proxy(protocol)
        if proxy is None:
            return socket.create_connection((target_host, target_port), timeout=timeout)
        proxy.validate()
        if proxy.mode == "socks5":
            return proxy._open_socks5_socket(target_host, target_port, timeout)
        return proxy._open_http_connect_socket(target_host, target_port, timeout)

    def _open_socks5_socket(
        self,
        target_host: str,
        target_port: int,
        timeout: float,
    ) -> socket.socket:
        try:
            import socks
        except ImportError as exc:  # PySocks
            raise ConfigurationError(
                "SOCKS5 proxy support requires PySocks. Reinstall GNSS Go dependencies."
            ) from exc
        sock = socks.socksocket()
        sock.set_proxy(
            socks.SOCKS5,
            self.host,
            self.port,
            rdns=True,
            username=self.username or None,
            password=self.password or None,
        )
        sock.settimeout(timeout)
        sock.connect((target_host, target_port))
        return sock

    def _open_http_connect_socket(
        self,
        target_host: str,
        target_port: int,
        timeout: float,
    ) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        try:
            headers = [
                f"CONNECT {target_host}:{target_port} HTTP/1.1",
                f"Host: {target_host}:{target_port}",
                "Proxy-Connection: Keep-Alive",
            ]
            if self.username:
                token = base64.b64encode(
                    f"{self.username}:{self.password}".encode("utf-8")
                ).decode("ascii")
                headers.append(f"Proxy-Authorization: Basic {token}")
            request = "\r\n".join(headers) + "\r\n\r\n"
            sock.sendall(request.encode("ascii"))
            response = bytearray()
            while b"\r\n\r\n" not in response and len(response) < 65536:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
            first = bytes(response).split(b"\r\n", 1)[0].decode("latin1", "replace")
            parts = first.split(" ", 2)
            status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            if status != 200:
                raise OSError(f"HTTP proxy CONNECT failed: {first or 'no response'}")
            return sock
        except Exception:
            sock.close()
            raise


def _http_test(config: ProxyConfig, timeout: float) -> str:
    proxy = config.proxy_url(protocol="https")
    trust_env = config.mode == "system" and config.use_for_http and proxy is None
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=timeout),
            follow_redirects=False,
            proxy=proxy,
            trust_env=trust_env,
        ) as client:
            response = client.get(
                "https://gps.csn.uchile.cl/",
                headers={"User-Agent": "Mozilla/5.0 GNSS Go proxy test"},
            )
        if response.status_code == 429:
            return "REACHABLE (CSN HTTP 429 rate limited)"
        if response.status_code < 500:
            return f"OK (HTTP {response.status_code})"
        return f"REACHABLE (HTTP {response.status_code})"
    except Exception as exc:
        return f"FAILED ({exc.__class__.__name__}: {exc})"


def _tcp_test(
    config: ProxyConfig,
    host: str,
    port: int,
    timeout: float,
    protocol: str,
) -> str:
    use_proxy = config.uses_tunnel_proxy(protocol)
    system_diag = config.system_proxy_diagnostic(protocol) if config.mode == "system" else ""
    try:
        if config.mode == "system" and config.enabled_for(protocol):
            sock = config.open_socket(host, port, timeout, protocol=protocol)
            route = f"system proxy: {system_diag}" if use_proxy else f"direct; system proxy {system_diag}"
        elif use_proxy:
            sock = config.open_socket(host, port, timeout, protocol=protocol)
            route = "proxy"
        else:
            sock = socket.create_connection((host, port), timeout=timeout)
            route = "direct"
        sock.close()
        return f"OK ({route})"
    except Exception as exc:
        if config.mode == "system" and config.enabled_for(protocol):
            route = f"system proxy: {system_diag}" if use_proxy else f"direct; system proxy {system_diag}"
        else:
            route = "proxy" if use_proxy else "direct"
        return f"FAILED ({route}; {exc.__class__.__name__}: {exc})"


def test_network_settings(settings: "NetworkSettings", timeout: float = 8.0) -> dict[str, str]:
    """Perform small, bounded connectivity tests for the troublesome archives."""
    config = ProxyConfig.from_settings(settings)
    config.validate()
    results = {
        "Proxy": config.display_url(),
    }
    try:
        from gnssgo.download.chromedriver import find_chromedriver

        detected_driver = find_chromedriver(getattr(settings, "chromedriver_path", ""))
        results["ChromeDriver"] = str(detected_driver) if detected_driver else "NOT FOUND"
    except Exception as exc:  # pragma: no cover - diagnostic only.
        results["ChromeDriver"] = f"ERROR ({exc})"
    if config.mode == "system":
        results["Detected system tunnel proxy"] = config.system_proxy_diagnostic("sftp")
    results.update({
        "Chile CSN HTTPS": _http_test(config, timeout),
        "Mexico INEGI SFTP :22": _tcp_test(
            config, "geodesia2.inegi.org.mx", 22, timeout, "sftp"
        ),
        "Uruguay IGM FTP :21": _tcp_test(config, "pp.igm.gub.uy", 21, timeout, "ftp"),
        "Uruguay IGM SFTP :2222": _tcp_test(
            config, "sftp.igm.gub.uy", 2222, timeout, "sftp"
        ),
    })
    return results
