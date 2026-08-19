from __future__ import annotations

from gnssgo.config import NetworkSettings
from gnssgo.network import ProxyConfig


def test_http_proxy_url_with_credentials() -> None:
    config = ProxyConfig.from_settings(
        NetworkSettings(
            mode="http",
            host="127.0.0.1",
            port=7890,
            username="a b",
            password="p@ss",
        )
    )
    assert config.proxy_url(protocol="https") == "http://a%20b:p%40ss@127.0.0.1:7890"
    assert config.display_url() == "http://a b@127.0.0.1:7890"


def test_socks5_curl_uses_remote_dns_scheme() -> None:
    config = ProxyConfig.from_settings(
        NetworkSettings(mode="socks5", host="localhost", port=1080)
    )
    assert config.proxy_url(protocol="https") == "socks5://localhost:1080"
    assert config.proxy_url(for_curl=True, protocol="sftp") == "socks5h://localhost:1080"


def test_direct_mode_disables_proxy() -> None:
    config = ProxyConfig.from_settings(
        NetworkSettings(mode="direct", host="127.0.0.1", port=7890)
    )
    assert config.proxy_url(protocol="https") is None
    assert config.enabled_for("sftp") is False


def test_legacy_proxy_is_migrated() -> None:
    config = ProxyConfig.from_settings(
        NetworkSettings(proxy="http://user:pw@proxy.example:3128")
    )
    assert config.mode == "http"
    assert config.host == "proxy.example"
    assert config.port == 3128
    assert config.username == "user"
    assert config.password == "pw"


def test_http_connect_proxy_socket(monkeypatch) -> None:
    import socket
    import threading

    target = socket.socket()
    target.bind(("127.0.0.1", 0))
    target.listen(1)
    target_port = target.getsockname()[1]

    proxy = socket.socket()
    proxy.bind(("127.0.0.1", 0))
    proxy.listen(1)
    proxy_port = proxy.getsockname()[1]
    seen = {}

    def serve_proxy() -> None:
        conn, _ = proxy.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(4096)
        seen["request"] = data.decode("latin1")
        upstream = socket.create_connection(("127.0.0.1", target_port), timeout=2)
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        # No full relay is needed: successful CONNECT and socket creation are
        # what Paramiko needs before it starts the SSH handshake.
        upstream.close()
        conn.close()

    thread = threading.Thread(target=serve_proxy, daemon=True)
    thread.start()
    config = ProxyConfig.from_settings(
        NetworkSettings(mode="http", host="127.0.0.1", port=proxy_port)
    )
    sock = config.open_socket("127.0.0.1", target_port, 2)
    sock.close()
    thread.join(timeout=2)
    proxy.close()
    target.close()
    assert f"CONNECT 127.0.0.1:{target_port} HTTP/1.1" in seen["request"]


def test_system_proxy_can_tunnel_sftp(monkeypatch) -> None:
    import socket
    import threading
    import gnssgo.network.proxy as proxy_module

    target = socket.socket()
    target.bind(("127.0.0.1", 0))
    target.listen(1)
    target_port = target.getsockname()[1]

    proxy = socket.socket()
    proxy.bind(("127.0.0.1", 0))
    proxy.listen(1)
    proxy_port = proxy.getsockname()[1]
    seen = {}

    def serve_proxy() -> None:
        conn, _ = proxy.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(4096)
        seen["request"] = data.decode("latin1")
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        conn.close()

    thread = threading.Thread(target=serve_proxy, daemon=True)
    thread.start()
    monkeypatch.setattr(
        proxy_module,
        "getproxies",
        lambda: {"https": f"http://127.0.0.1:{proxy_port}"},
    )
    config = ProxyConfig.from_settings(
        NetworkSettings(mode="system", use_for_sftp=True)
    )
    assert config.uses_tunnel_proxy("sftp") is True
    sock = config.open_socket("127.0.0.1", target_port, 2, protocol="sftp")
    sock.close()
    thread.join(timeout=2)
    proxy.close()
    target.close()
    assert f"CONNECT 127.0.0.1:{target_port} HTTP/1.1" in seen["request"]


def test_system_proxy_uses_windows_registry_when_urllib_empty(monkeypatch) -> None:
    import gnssgo.network.proxy as proxy_module

    monkeypatch.setattr(proxy_module, "getproxies", lambda: {})
    monkeypatch.setattr(
        ProxyConfig,
        "_windows_registry_proxy_info",
        staticmethod(lambda: ({"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}, None)),
    )
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    config = ProxyConfig.from_settings(NetworkSettings(mode="system", use_for_sftp=True))
    assert config.uses_tunnel_proxy("sftp") is True
    assert "127.0.0.1:7890" in config.system_proxy_diagnostic("sftp")
    assert "Windows registry" in config.system_proxy_diagnostic("sftp")


def test_parse_windows_per_protocol_proxy_server() -> None:
    parsed = ProxyConfig._parse_windows_proxy_server(
        "http=127.0.0.1:7890;https=127.0.0.1:7890;socks=127.0.0.1:7891"
    )
    assert parsed["http"] == "http://127.0.0.1:7890"
    assert parsed["https"] == "http://127.0.0.1:7890"
    assert parsed["socks"] == "socks5://127.0.0.1:7891"


def test_system_proxy_diagnostic_reports_pac(monkeypatch) -> None:
    import gnssgo.network.proxy as proxy_module

    monkeypatch.setattr(proxy_module, "getproxies", lambda: {})
    monkeypatch.setattr(
        ProxyConfig,
        "_windows_registry_proxy_info",
        staticmethod(lambda: ({}, "http://127.0.0.1:7890/proxy.pac")),
    )
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    config = ProxyConfig.from_settings(NetworkSettings(mode="system", use_for_sftp=True))
    assert "PAC configured" in config.system_proxy_diagnostic("sftp")
