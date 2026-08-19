from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import webbrowser
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from gnssgo.exceptions import DownloadError, RemoteFileNotFound
from gnssgo.network import ProxyConfig


class _LocalWebSocket:
    """Tiny RFC6455 client sufficient for localhost Chrome DevTools Protocol.

    This avoids adding a browser-automation dependency just to talk to an Edge/
    Chrome instance that is already installed on Windows.
    """

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "ws":
            raise DownloadError(f"Unsupported DevTools WebSocket URL: {url}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.timeout = timeout
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buffer = bytearray()
        self._handshake()

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: http://localhost\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        raw = bytearray()
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > 65536:
                break
        head, sep, tail = bytes(raw).partition(b"\r\n\r\n")
        first = head.split(b"\r\n", 1)[0].decode("latin-1", "replace") if head else ""
        if not sep or " 101 " not in f" {first} ":
            raise DownloadError(f"Chrome DevTools WebSocket handshake failed: {first or 'no response'}")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        headers = {}
        for line in head.split(b"\r\n")[1:]:
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
        if headers.get("sec-websocket-accept", "") != expected:
            raise DownloadError("Chrome DevTools WebSocket validation failed.")
        if tail:
            self._buffer.extend(tail)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def settimeout(self, timeout: float) -> None:
        self.sock.settimeout(timeout)

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                raise ConnectionError("DevTools WebSocket closed unexpectedly")
            self._buffer.extend(chunk)
        out = bytes(self._buffer[:count])
        del self._buffer[:count]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        first = 0x80 | (opcode & 0x0F)
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes([first, 0x80 | length])
        elif length <= 0xFFFF:
            header = bytes([first, 0x80 | 126]) + struct.pack("!H", length)
        else:
            header = bytes([first, 0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self) -> str:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("DevTools WebSocket was closed")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                return payload.decode("utf-8", "replace")


class _CDPClient:
    def __init__(self, ws_url: str, timeout: float = 10.0) -> None:
        self.ws = _LocalWebSocket(ws_url, timeout=timeout)
        self._next_id = 1
        self.events: list[dict] = []

    def close(self) -> None:
        self.ws.close()

    def command(self, method: str, params: dict | None = None, timeout: float = 10.0) -> dict:
        ident = self._next_id
        self._next_id += 1
        payload = {"id": ident, "method": method}
        if params:
            payload["params"] = params
        self.ws.send_text(json.dumps(payload, separators=(",", ":")))
        deadline = time.monotonic() + timeout
        old_timeout = self.ws.sock.gettimeout()
        try:
            while time.monotonic() < deadline:
                self.ws.settimeout(max(0.1, deadline - time.monotonic()))
                msg = json.loads(self.ws.recv_text())
                if msg.get("id") == ident:
                    if "error" in msg:
                        raise DownloadError(
                            f"Chrome DevTools {method} failed: {msg['error']}"
                        )
                    return msg.get("result", {})
                if "method" in msg:
                    self.events.append(msg)
        finally:
            self.ws.settimeout(old_timeout if old_timeout is not None else timeout)
        raise DownloadError(f"Chrome DevTools command timed out: {method}")

    def drain_one(self, timeout: float = 0.05) -> dict | None:
        if self.events:
            return self.events.pop(0)
        old_timeout = self.ws.sock.gettimeout()
        try:
            self.ws.settimeout(timeout)
            try:
                msg = json.loads(self.ws.recv_text())
            except socket.timeout:
                return None
            if "method" in msg:
                return msg
            return None
        finally:
            self.ws.settimeout(old_timeout if old_timeout is not None else 10.0)


def find_chromium_browser() -> Path | None:
    """Locate installed Edge/Chrome, preferring Microsoft Edge on Windows."""
    candidates: list[Path] = []
    if os.name == "nt":
        for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env_name)
            if not base:
                continue
            candidates.extend(
                [
                    Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                    Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
                ]
            )
    for name in ("msedge.exe", "msedge", "chrome.exe", "chrome", "google-chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    seen: set[str] = set()
    for item in candidates:
        key = str(item).lower()
        if key in seen:
            continue
        seen.add(key)
        if item.is_file():
            return item
    return None


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str, *, method: str = "GET", timeout: float = 2.0) -> dict:
    # DevTools is always on localhost; never send these control requests through
    # the user's system proxy.
    request = Request(url, method=method)
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_debug_json(url: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _get_json(url, timeout=min(1.0, timeout))
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise DownloadError(f"Could not connect to local Edge/Chrome DevTools: {last_error}")


def _browser_proxy_argument(proxy: ProxyConfig) -> str | None:
    if not proxy.enabled_for("https"):
        return None
    if proxy.mode == "system":
        # Let Chromium consume the same Windows WinINet proxy configuration as
        # the user's ordinary Edge/Chrome session.
        return None
    if not proxy.custom:
        return None
    if proxy.username or proxy.password:
        # Chromium CLI does not accept authenticated proxy credentials safely.
        # Leave this case to the normal downloader.
        return None
    scheme = "socks5" if proxy.mode == "socks5" else "http"
    return f"{scheme}://{proxy.host}:{proxy.port}"


def _persistent_csn_profile_dir() -> Path:
    """Return a persistent, dedicated browser profile for Chile CSN.

    A temporary/headless Chromium profile is easy for some front ends to treat
    differently from an ordinary interactive browser.  Keep a small GNSS Go-only
    profile so cookies/site state survive between downloads without touching the
    user's real Edge/Chrome profile.
    """
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return root / "GNSS Go" / "csn-browser-profile"
    return Path.home() / ".gnssgo" / "csn-browser-profile"


def _profile_is_initialized(profile_dir: Path) -> bool:
    return (profile_dir / ".gnssgo_csn_initialized").exists()


def _mark_profile_initialized(profile_dir: Path) -> None:
    try:
        (profile_dir / ".gnssgo_csn_initialized").write_text("1", encoding="ascii")
    except OSError:
        pass



def _browser_download_directories() -> list[Path]:
    """Return likely download folders for the user's ordinary browser session.

    CSN can distinguish a GNSS Go-controlled Chromium profile from the user's
    normal browser profile.  The browser-assisted transport therefore opens the
    file URL in the user's existing/default browser and watches the browser's
    normal download folder.  Prefer explicit Edge/Chrome profile settings and
    the Windows Known Downloads folder, with ``~/Downloads`` as a fallback.
    """
    candidates: list[Path] = []

    if os.name == "nt":
        # Windows Known Folder: Downloads.  Explorer stores redirected Downloads
        # locations under this GUID in User Shell Folders.
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                raw, _ = winreg.QueryValueEx(
                    key,
                    "{374DE290-123F-4565-9164-39C4925E467B}",
                )
            if raw:
                candidates.append(Path(os.path.expandvars(str(raw))).expanduser())
        except Exception:
            pass

        local = os.environ.get("LOCALAPPDATA")
        if local:
            user_data_roots = [
                Path(local) / "Microsoft" / "Edge" / "User Data",
                Path(local) / "Google" / "Chrome" / "User Data",
            ]
            for root in user_data_roots:
                if not root.exists():
                    continue
                profiles = [root / "Default", *sorted(root.glob("Profile *"))]
                for profile in profiles:
                    prefs = profile / "Preferences"
                    if not prefs.exists():
                        continue
                    try:
                        data = json.loads(prefs.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    download = data.get("download") if isinstance(data, dict) else None
                    savefile = data.get("savefile") if isinstance(data, dict) else None
                    for section in (download, savefile):
                        if not isinstance(section, dict):
                            continue
                        raw = section.get("default_directory")
                        if raw:
                            candidates.append(
                                Path(os.path.expandvars(str(raw))).expanduser()
                            )

    candidates.append(Path.home() / "Downloads")

    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        try:
            resolved = item.expanduser()
            key = str(resolved).casefold()
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique



def _chromium_history_files() -> list[Path]:
    """Return Edge/Chrome History databases for all local Windows profiles.

    The browser may save downloads to a custom folder that is not the Windows
    Known Downloads folder and may not be visible in ``Preferences`` while the
    browser is running.  Chromium records the *actual* target path in each
    profile's ``History`` SQLite database, so this is the most reliable way to
    recover a browser-assisted CSN download after Windows opens the user's normal
    browser session.
    """
    if os.name != "nt":
        return []

    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []

    roots = [
        Path(local) / "Microsoft" / "Edge" / "User Data",
        Path(local) / "Google" / "Chrome" / "User Data",
    ]
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        profiles = [root / "Default", *sorted(root.glob("Profile *")), root / "Guest Profile"]
        for profile in profiles:
            history = profile / "History"
            if history.exists() and history.is_file():
                out.append(history)
    return out


def _open_history_readonly(path: Path) -> tuple[sqlite3.Connection, Path | None]:
    """Open a Chromium History DB without modifying the user's profile.

    Chromium normally permits concurrent readers.  If Windows sharing/locking
    prevents that, copy the database and WAL sidecars to a temporary directory
    and read the copy instead.
    """
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=0.2)
        conn.execute("PRAGMA query_only=ON")
        return conn, None
    except sqlite3.Error:
        tmp = Path(tempfile.mkdtemp(prefix="gnssgo-history-"))
        copied = tmp / "History"
        shutil.copy2(path, copied)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                try:
                    shutil.copy2(sidecar, Path(str(copied) + suffix))
                except OSError:
                    pass
        conn = sqlite3.connect(str(copied), timeout=0.2)
        return conn, tmp


def _history_download_targets(
    url: str,
    expected_filename: str,
    *,
    opened_at_ns: int,
) -> list[Path]:
    """Return actual recent Edge/Chrome target paths for one download URL.

    The exact target path is recorded by Chromium even when the browser has a
    custom download directory (D:, OneDrive, Desktop, etc.).  URL matching is
    preferred; filename matching is only a fallback for older History schemas.
    """
    out: list[Path] = []
    seen: set[str] = set()
    expected_lower = Path(expected_filename).name.casefold()
    opened_at_s = opened_at_ns / 1_000_000_000

    for history in _chromium_history_files():
        conn: sqlite3.Connection | None = None
        tmp_history_dir: Path | None = None
        try:
            conn, tmp_history_dir = _open_history_readonly(history)
            rows: list[tuple] = []
            try:
                rows = list(
                    conn.execute(
                        """
                        SELECT d.current_path, d.target_path, d.start_time, d.end_time,
                               d.received_bytes, d.total_bytes, d.state, c.url
                          FROM downloads AS d
                          JOIN downloads_url_chains AS c ON c.id = d.id
                         WHERE c.url = ?
                         ORDER BY d.start_time DESC, c.chain_index DESC
                         LIMIT 20
                        """,
                        (url,),
                    )
                )
            except sqlite3.Error:
                rows = []

            # Older Chromium versions or unusual redirects may not retain the
            # exact URL chain.  Search recent records by filename as a fallback.
            if not rows:
                try:
                    rows = list(
                        conn.execute(
                            """
                            SELECT current_path, target_path, start_time, end_time,
                                   received_bytes, total_bytes, state, ''
                              FROM downloads
                             ORDER BY start_time DESC
                             LIMIT 50
                            """
                        )
                    )
                except sqlite3.Error:
                    rows = []

            for row in rows:
                current_path, target_path, start_time, _end_time, received, total, state, row_url = row
                # Chrome timestamps are microseconds since 1601-01-01 UTC.
                try:
                    start_unix = float(start_time) / 1_000_000.0 - 11_644_473_600.0
                except Exception:
                    start_unix = 0.0
                if start_unix and start_unix < opened_at_s - 15.0:
                    continue

                candidates = [target_path, current_path]
                for raw in candidates:
                    if not raw:
                        continue
                    candidate = Path(str(raw))
                    name = candidate.name.casefold()
                    filename_matches = bool(_download_name_regex(expected_filename).match(candidate.name))
                    url_matches = str(row_url or "") == url
                    if not (url_matches or filename_matches or name == expected_lower):
                        continue
                    key = str(candidate).casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(candidate)
        except (OSError, sqlite3.Error):
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if tmp_history_dir is not None:
                shutil.rmtree(tmp_history_dir, ignore_errors=True)
    return out


def _copy_stable_download(
    source: Path,
    output_path: Path,
    stable: dict[str, tuple[int, int]],
) -> bool:
    """Copy a completed browser download after its size is stable for 3 polls."""
    try:
        if not source.exists() or not source.is_file():
            return False
        if source.name.lower().endswith((".crdownload", ".tmp", ".download")):
            return False
        stat = source.stat()
    except OSError:
        return False
    if stat.st_size <= 0:
        return False

    key = str(source).casefold()
    old = stable.get(key)
    if old and old[0] == stat.st_size:
        count = old[1] + 1
    else:
        count = 1
    stable[key] = (stat.st_size, count)
    if count < 3:
        return False

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output_path)
    except OSError:
        return False
    if not output_path.exists() or output_path.stat().st_size <= 0:
        return False

    # The browser copy is only a staging file.  Remove it when possible so the
    # user's configured GNSS Go output directory remains the authoritative copy.
    try:
        source.unlink()
    except OSError:
        pass
    return True


def _download_name_regex(expected_filename: str) -> re.Pattern[str]:
    """Match Chrome/Edge duplicate-name variants for one expected file."""
    name = Path(expected_filename).name
    suffix = Path(name).suffix
    root = name[: -len(suffix)] if suffix else name
    return re.compile(
        rf"^{re.escape(root)}(?: \(\d+\))?{re.escape(suffix)}$",
        re.IGNORECASE,
    )


def _matching_browser_files(directory: Path, expected_filename: str) -> list[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    matcher = _download_name_regex(expected_filename)
    out: list[Path] = []
    try:
        for item in directory.iterdir():
            if not item.is_file():
                continue
            name = item.name
            if name.lower().endswith((".crdownload", ".tmp", ".download")):
                continue
            if matcher.match(name):
                out.append(item)
    except OSError:
        return []
    return out


def _snapshot_browser_files(
    directories: list[Path], expected_filename: str
) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for directory in directories:
        for item in _matching_browser_files(directory, expected_filename):
            try:
                stat = item.stat()
            except OSError:
                continue
            snapshot[str(item).casefold()] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _open_url_in_default_browser(url: str) -> None:
    """Open a URL in the user's *normal* default browser/profile.

    On Windows, ``os.startfile``/ShellExecute reuses an already-running Edge or
    Chrome profile when possible.  That is deliberately different from CDP,
    remote-debugging, Selenium, or a GNSS Go-specific browser profile.
    """
    if os.name == "nt" and hasattr(os, "startfile"):
        os.startfile(url)  # type: ignore[attr-defined]
        return
    if not webbrowser.open(url, new=0, autoraise=True):
        raise DownloadError("Could not open the default browser for Chile CSN download.")


def download_with_user_browser_session(
    url: str,
    output_path: Path,
    *,
    expected_filename: str | None = None,
    timeout: float = 120.0,
) -> Path:
    """Download a CSN file through the user's ordinary browser session.

    The URL is opened via the operating-system URL handler instead of launching
    an automated Chromium instance.  GNSS Go then watches the browser's normal
    download folders, imports the newly downloaded file into ``output_path`` and
    leaves all HTTP/TLS/proxy/cookie decisions to the user's real browser.
    """
    if os.name != "nt":
        raise DownloadError(
            "Chile CSN browser-assisted download currently requires Windows."
        )

    expected = expected_filename or Path(urlparse(url).path).name
    if not expected:
        raise DownloadError("Chile CSN URL does not contain a download filename.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    directories = _browser_download_directories()
    if not directories:
        raise DownloadError("Could not determine the browser Downloads folder.")
    before = _snapshot_browser_files(directories, expected)
    opened_at_ns = time.time_ns()

    try:
        _open_url_in_default_browser(url)
    except OSError as exc:
        raise DownloadError(f"Could not open Chile CSN URL in the default browser: {exc}") from exc

    deadline = time.monotonic() + max(5.0, timeout)
    stable: dict[str, tuple[int, int]] = {}
    last_history_scan = 0.0
    history_targets: list[Path] = []
    while time.monotonic() < deadline:
        # First inspect the browser's History database.  Chromium records the
        # exact target_path selected by the user's real profile, which is more
        # reliable than guessing ~/Downloads when the user has configured a
        # custom drive/folder.  Refresh at a modest cadence to avoid repeatedly
        # opening a live SQLite DB while the browser is writing it.
        now = time.monotonic()
        if now - last_history_scan >= 0.75:
            last_history_scan = now
            history_targets = _history_download_targets(
                url, expected, opened_at_ns=opened_at_ns
            )
        for item in history_targets:
            if _copy_stable_download(item, output_path, stable):
                return output_path

        # Retain the existing folder watcher as a fallback for browsers/history
        # schemas that do not expose a usable target_path.
        for directory in directories:
            for item in _matching_browser_files(directory, expected):
                try:
                    stat = item.stat()
                except OSError:
                    continue
                key = str(item).casefold()
                previous = before.get(key)
                changed = previous is None or previous != (stat.st_mtime_ns, stat.st_size)
                # Some filesystems have coarse timestamp resolution.  A file
                # created after launch with a non-zero size is also acceptable.
                recent = stat.st_mtime_ns >= opened_at_ns - 2_000_000_000
                if not changed and not recent:
                    continue
                if _copy_stable_download(item, output_path, stable):
                    return output_path
        time.sleep(0.25)

    searched = ", ".join(str(path) for path in directories)
    history_list = ", ".join(str(path) for path in history_targets) or "none"
    raise DownloadError(
        "Chile CSN URL was opened in your normal browser, but GNSS Go did not "
        f"import a completed {expected} download within {int(timeout)} s. "
        "The browser may have downloaded the file successfully to a custom "
        "location, but no recent Edge/Chrome History target could be imported. "
        f"Folder candidates: {searched}. Recent browser targets: {history_list}"
    )


def download_with_installed_chromium(
    url: str,
    output_path: Path,
    *,
    proxy: ProxyConfig,
    timeout: float = 120.0,
    user_agent: str | None = None,
) -> Path:
    """Download one public file through the installed Chromium browser engine.

    CSN can serve valid GNSS files to an ordinary interactive browser while
    returning 429/503 to scripted or headless clients.  Use a dedicated,
    persistent *headed* Edge/Chrome profile (started minimized) so the transfer
    follows the normal browser network/rendering path and retains CSN site state.
    """
    browser = find_chromium_browser()
    if browser is None:
        raise DownloadError("Microsoft Edge/Google Chrome was not found for CSN browser download.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    port = _free_local_port()

    # Use a *headed* Chromium instance with a persistent GNSS Go-only
    # profile.  The user has verified that the exact CSN file URL downloads in
    # ordinary Edge/Chrome while fresh headless Chromium receives HTTP 503.
    # Keeping the browser headed (started minimized) preserves the ordinary
    # browser execution path and allows CSN cookies/site state to persist.
    profile_dir = _persistent_csn_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    first_profile_use = not _profile_is_initialized(profile_dir)

    with tempfile.TemporaryDirectory(prefix="gnssgo-csn-download-") as temp:
        temp_root = Path(temp)
        download_dir = temp_root / "downloads"
        download_dir.mkdir()
        command = [
            str(browser),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-component-update",
            "--disable-sync",
            "--start-minimized",
            "--remote-allow-origins=*",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--profile-directory=Default",
            "about:blank",
        ]
        proxy_arg = _browser_proxy_argument(proxy)
        if proxy_arg:
            command.insert(-1, f"--proxy-server={proxy_arg}")

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        browser_client: _CDPClient | None = None
        page_client: _CDPClient | None = None
        try:
            version = _wait_debug_json(f"http://127.0.0.1:{port}/json/version", min(10.0, timeout))
            browser_ws = str(version.get("webSocketDebuggerUrl") or "")
            if not browser_ws:
                raise DownloadError("Edge/Chrome did not expose a DevTools WebSocket URL.")
            browser_client = _CDPClient(browser_ws, timeout=10.0)
            browser_client.command(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(download_dir),
                    "eventsEnabled": True,
                },
            )

            encoded = quote("about:blank", safe=":/")
            page = _get_json(
                f"http://127.0.0.1:{port}/json/new?{encoded}",
                method="PUT",
                timeout=3.0,
            )
            page_ws = str(page.get("webSocketDebuggerUrl") or "")
            if not page_ws:
                raise DownloadError("Edge/Chrome did not create a downloadable page target.")
            page_client = _CDPClient(page_ws, timeout=10.0)
            page_client.command("Page.enable")
            page_client.command("Network.enable")
            # Do not override the browser User-Agent for CSN.  A synthetic UA
            # that does not match the installed Edge/Chrome build is itself an
            # automation fingerprint; use the browser's native UA/TLS stack.
            # The deprecated page-scoped command remains useful across Edge
            # versions, so set both browser- and page-level download behaviour.
            try:
                page_client.command(
                    "Page.setDownloadBehavior",
                    {"behavior": "allow", "downloadPath": str(download_dir)},
                )
            except DownloadError:
                pass
            if first_profile_use:
                # Prime only the dedicated profile's first use.  This lets a
                # normal headed browser execute any CSN front-end JavaScript and
                # retain first-party cookies before the direct file navigation.
                try:
                    page_client.command(
                        "Page.navigate",
                        {"url": "https://gps.csn.uchile.cl/"},
                        timeout=15.0,
                    )
                    time.sleep(2.5)
                except DownloadError:
                    # The file navigation below is authoritative; a warm-up
                    # failure must not prevent trying the known direct file URL.
                    pass
                _mark_profile_initialized(profile_dir)

            nav = page_client.command("Page.navigate", {"url": url}, timeout=15.0)
            if nav.get("errorText"):
                raise DownloadError(f"Edge/Chrome navigation failed: {nav['errorText']}")

            deadline = time.monotonic() + timeout
            response_status: int | None = None
            response_url = url
            while time.monotonic() < deadline:
                # Consume network events so an HTML 404/429/503 is reported
                # accurately instead of looking like a generic browser timeout.
                event = page_client.drain_one(timeout=0.05)
                if event and event.get("method") == "Network.responseReceived":
                    response = event.get("params", {}).get("response", {})
                    event_url = str(response.get("url") or "")
                    if event_url.rstrip("/") == url.rstrip("/"):
                        try:
                            response_status = int(response.get("status"))
                        except (TypeError, ValueError):
                            pass
                        response_url = event_url or url

                completed = [
                    item
                    for item in download_dir.iterdir()
                    if item.is_file() and not item.name.endswith(".crdownload")
                ]
                partials = [item for item in download_dir.iterdir() if item.name.endswith(".crdownload")]
                if completed and not partials:
                    # A single direct navigation should produce one file.
                    downloaded = max(completed, key=lambda item: item.stat().st_mtime)
                    if downloaded.stat().st_size <= 0:
                        raise DownloadError("Edge/Chrome downloaded an empty CSN file.")
                    shutil.move(str(downloaded), str(output_path))
                    return output_path

                if response_status and response_status >= 400 and not partials:
                    if response_status == 404:
                        raise RemoteFileNotFound(f"Remote file not found: {response_url}")
                    raise DownloadError(
                        f"Chile CSN returned HTTP {response_status} in Edge/Chrome for {response_url}."
                    )
                time.sleep(0.05)

            raise DownloadError(
                f"Edge/Chrome did not complete the CSN file download within {int(timeout)} s."
                + (f" Last HTTP status: {response_status}." if response_status else "")
            )
        finally:
            # Close Chromium gracefully first so the dedicated CSN profile can
            # flush cookies/site state to disk for the next download.
            if browser_client is not None:
                try:
                    browser_client.command("Browser.close", timeout=2.0)
                except Exception:
                    pass
            if page_client is not None:
                page_client.close()
            if browser_client is not None:
                browser_client.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
