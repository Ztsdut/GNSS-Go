from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from gnssgo.exceptions import DownloadError, RemoteFileNotFound
from gnssgo.network import ProxyConfig


def find_chromedriver(explicit_path: str | Path | None = None) -> Path | None:
    """Locate a standalone ChromeDriver executable.

    Search order is deliberately predictable so a user can pin a matching
    driver without modifying PATH:

    1. explicit path from GNSS Go settings;
    2. ``GNSSGO_CHROMEDRIVER`` / ``CHROMEDRIVER`` environment variables;
    3. common project-local ``tools`` locations;
    4. the current PATH.
    """
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    for env_name in ("GNSSGO_CHROMEDRIVER", "CHROMEDRIVER"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value).expanduser())

    # Useful for source checkouts and portable builds.
    here = Path(__file__).resolve()
    for base in (Path.cwd(), here.parents[3] if len(here.parents) >= 4 else here.parent):
        candidates.extend(
            [
                base / "chromedriver.exe",
                base / "chromedriver",
                base / "tools" / "chromedriver.exe",
                base / "tools" / "chromedriver",
                base / "drivers" / "chromedriver.exe",
                base / "drivers" / "chromedriver",
            ]
        )

    for name in ("chromedriver.exe", "chromedriver"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    seen: set[str] = set()
    for item in candidates:
        try:
            resolved = item.resolve()
        except OSError:
            resolved = item
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file():
            return resolved
    return None


def find_chrome_browser() -> Path | None:
    """Locate Google Chrome (ChromeDriver does not control Microsoft Edge)."""
    candidates: list[Path] = []
    if os.name == "nt":
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for name in ("chrome.exe", "chrome", "google-chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for item in candidates:
        if item.is_file():
            return item
    return None


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _local_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 5.0,
) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=data, headers=headers, method=method)
    # WebDriver is localhost control traffic and must never be sent through the
    # user's configured/system proxy.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        message = _webdriver_error_message(body) or f"HTTP {exc.code}"
        raise DownloadError(f"ChromeDriver request failed: {message}") from exc
    except URLError as exc:
        raise DownloadError(f"Could not communicate with ChromeDriver: {exc}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DownloadError("ChromeDriver returned a non-JSON response.") from exc


def _webdriver_error_message(body: dict) -> str:
    value = body.get("value") if isinstance(body, dict) else None
    if isinstance(value, dict):
        return str(value.get("message") or value.get("error") or "").strip()
    return ""


def _wait_driver(port: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = _local_json(f"http://127.0.0.1:{port}/status", timeout=1.0)
            value = response.get("value", {})
            if not isinstance(value, dict) or value.get("ready", True):
                return
        except Exception as exc:  # noqa: BLE001 - bounded startup polling.
            last = exc
        time.sleep(0.1)
    raise DownloadError(f"ChromeDriver did not become ready: {last}")


def _chrome_proxy_arg(proxy: ProxyConfig) -> str | None:
    """Return a Chrome ``--proxy-server`` value for custom GNSS Go proxies.

    In System mode Chrome consumes Windows/OS proxy settings by itself, which is
    exactly what the user expects.  Direct mode bypasses proxy resolution.
    """
    if proxy.mode == "system":
        return None
    if proxy.mode == "direct" or not proxy.enabled_for("https"):
        return None
    if not proxy.custom:
        return None
    if proxy.username or proxy.password:
        raise DownloadError(
            "ChromeDriver transport does not support authenticated browser proxy "
            "credentials. Use System proxy or an unauthenticated local HTTP/SOCKS5 proxy."
        )
    scheme = "socks5" if proxy.mode == "socks5" else "http"
    return f"{scheme}://{proxy.host}:{proxy.port}"


def _create_session(
    port: int,
    *,
    chrome: Path | None,
    download_dir: Path,
    proxy: ProxyConfig,
    allow_popups: bool = False,
) -> str:
    args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-component-update",
        "--disable-sync",
        "--start-minimized",
    ]
    if proxy.mode == "direct":
        args.append("--no-proxy-server")
    else:
        proxy_arg = _chrome_proxy_arg(proxy)
        if proxy_arg:
            args.append(f"--proxy-server={proxy_arg}")

    options: dict = {
        "args": args,
        "prefs": {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "profile.default_content_settings.popups": 0,
        },
    }
    # Terras may open the parameter/download page in a new tab/window.  Only
    # GEONET opts into popup allowance; other browser transports retain their
    # previous Chrome preference behavior.
    if allow_popups:
        options["prefs"]["profile.default_content_setting_values.popups"] = 1
    if chrome is not None:
        options["binary"] = str(chrome)

    body = {
        "capabilities": {
            "alwaysMatch": {
                "browserName": "chrome",
                "goog:chromeOptions": options,
            }
        }
    }
    response = _local_json(
        f"http://127.0.0.1:{port}/session",
        method="POST",
        payload=body,
        timeout=20.0,
    )
    value = response.get("value") if isinstance(response, dict) else None
    session_id = ""
    if isinstance(value, dict):
        session_id = str(value.get("sessionId") or "")
    session_id = session_id or str(response.get("sessionId") or "")
    if not session_id:
        message = _webdriver_error_message(response)
        raise DownloadError(
            "ChromeDriver did not create a Chrome session"
            + (f": {message}" if message else ".")
        )
    return session_id


def _execute_cdp(port: int, session_id: str, cmd: str, params: dict) -> None:
    try:
        _local_json(
            f"http://127.0.0.1:{port}/session/{session_id}/goog/cdp/execute",
            method="POST",
            payload={"cmd": cmd, "params": params},
            timeout=5.0,
        )
    except DownloadError:
        # Chrome preferences already specify the download directory.  CDP is a
        # compatibility reinforcement, not a hard dependency.
        pass


def _navigate(port: int, session_id: str, url: str) -> None:
    try:
        _local_json(
            f"http://127.0.0.1:{port}/session/{session_id}/url",
            method="POST",
            payload={"url": url},
            timeout=20.0,
        )
    except DownloadError as exc:
        # Direct navigation to a downloadable response often ends with
        # net::ERR_ABORTED because Chrome hands the response to the download
        # manager.  The actual file-system result below is authoritative.
        message = str(exc)
        if "ERR_ABORTED" not in message and "aborted" not in message.lower():
            raise


def _delete_session(port: int, session_id: str) -> None:
    try:
        _local_json(
            f"http://127.0.0.1:{port}/session/{session_id}",
            method="DELETE",
            timeout=3.0,
        )
    except Exception:
        pass


def _wait_for_download(
    download_dir: Path,
    expected_filename: str,
    *,
    started_ns: int,
    timeout: float,
    stall_timeout: float = 90.0,
    progress_callback: Callable[[int], None] | None = None,
    cancellation_check: Callable[[], None] | None = None,
) -> Path:
    """Wait for Chrome to finish a download using *progress*, not a short wall clock.

    ``timeout`` is only the absolute safety ceiling (30 minutes for CSN from the
    caller).  As long as either the final file or ``.crdownload`` keeps growing,
    the transfer is considered healthy.  A transfer is treated as stalled only
    when no byte progress is observed for ``stall_timeout`` seconds.
    """
    expected = download_dir / expected_filename
    partial = download_dir / f"{expected_filename}.crdownload"
    started_at = time.monotonic()
    deadline = started_at + max(float(timeout), 1.0)
    stall_timeout = max(float(stall_timeout), 1.0)
    last_progress_at = started_at
    last_size = -1
    stable_since: float | None = None

    while True:
        now = time.monotonic()
        if cancellation_check is not None:
            cancellation_check()

        if now >= deadline:
            raise DownloadError(
                "Chile CSN ChromeDriver download exceeded the maximum allowed "
                f"time of {int(timeout)} s for {expected_filename}."
            )

        current_size = 0
        current_path: Path | None = None
        # During an active Chrome download the .crdownload file is authoritative.
        # Once it disappears, the final filename becomes authoritative.
        if partial.exists():
            current_path = partial
        elif expected.exists():
            current_path = expected

        if current_path is not None:
            try:
                stat = current_path.stat()
            except OSError:
                time.sleep(0.15)
                continue
            if current_path == expected and stat.st_mtime_ns + 2_000_000_000 < started_ns:
                # Old file from a previous run; do not report it as this transfer.
                time.sleep(0.15)
                continue
            current_size = max(0, int(stat.st_size))

        if current_size != last_size:
            # Any size change is forward activity.  Reset the no-progress timer.
            last_size = current_size
            last_progress_at = now
            stable_since = None
            if progress_callback is not None and current_size > 0:
                progress_callback(current_size)

        if expected.exists() and not partial.exists():
            try:
                stat = expected.stat()
            except OSError:
                stat = None
            if stat is not None and stat.st_size > 0:
                if stable_since is None:
                    stable_since = now
                elif now - stable_since >= 0.5:
                    if progress_callback is not None:
                        progress_callback(int(stat.st_size))
                    return expected
        else:
            stable_since = None

        if now - last_progress_at >= stall_timeout:
            raise DownloadError(
                "Chile CSN ChromeDriver download stalled: no file-size progress for "
                f"{int(stall_timeout)} s while waiting for {expected_filename}."
            )

        time.sleep(0.15)


def download_with_chromedriver(
    url: str,
    output_path: Path,
    *,
    expected_filename: str,
    proxy: ProxyConfig,
    timeout: float = 1800.0,
    stall_timeout: float = 90.0,
    chromedriver_path: str | Path | None = None,
    progress_callback: Callable[[int], None] | None = None,
    cancellation_check: Callable[[], None] | None = None,
) -> Path:
    """Download a CSN file with standalone ChromeDriver into GNSS Go's folder.

    Unlike the previous normal-browser import path, Chrome is configured *before*
    navigation so its download manager writes directly into the GNSS Go output
    directory.  There is no browser-History database polling or post-hoc search.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or parsed.hostname != "gps.csn.uchile.cl":
        raise DownloadError("ChromeDriver CSN transport only accepts official CSN HTTPS URLs.")

    driver = find_chromedriver(chromedriver_path)
    if driver is None:
        raise DownloadError(
            "ChromeDriver was not found. Put a ChromeDriver version matching your installed "
            "Google Chrome in PATH, set GNSSGO_CHROMEDRIVER, or configure its path in "
            "Settings → Network."
        )
    chrome = find_chrome_browser()
    if chrome is None:
        raise DownloadError(
            "Google Chrome was not found. Standalone chromedriver.exe controls Google Chrome; "
            "install Chrome or use a matching Chrome/ChromeDriver installation."
        )

    download_dir = output_path.parent
    download_dir.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    expected_path = download_dir / Path(expected_filename).name
    # A task is created only when GNSS Go intends to fetch the remote file.  A
    # leftover browser file with the same name would make completion detection
    # ambiguous, so remove it; the authoritative final GNSS Go file has a
    # separate task.destination and is handled by the download manager.
    if expected_path != output_path:
        try:
            expected_path.unlink(missing_ok=True)
        except OSError as exc:
            raise DownloadError(f"Could not prepare Chrome download target {expected_path}: {exc}") from exc
    (download_dir / f"{expected_path.name}.crdownload").unlink(missing_ok=True)

    port = _free_local_port()
    command = [str(driver), f"--port={port}", "--allowed-ips="]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    session_id = ""
    try:
        _wait_driver(port, timeout=min(10.0, timeout))
        session_id = _create_session(
            port,
            chrome=chrome,
            download_dir=download_dir,
            proxy=proxy,
        )
        _execute_cdp(
            port,
            session_id,
            "Browser.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(download_dir.resolve()),
                "eventsEnabled": False,
            },
        )
        started_ns = time.time_ns()
        _navigate(port, session_id, url)
        downloaded = _wait_for_download(
            download_dir,
            expected_path.name,
            started_ns=started_ns,
            timeout=timeout,
            stall_timeout=stall_timeout,
            progress_callback=progress_callback,
            cancellation_check=cancellation_check,
        )
        if downloaded.stat().st_size <= 0:
            raise DownloadError("ChromeDriver downloaded an empty Chile CSN file.")
        shutil.move(str(downloaded), str(output_path))
        return output_path
    finally:
        if session_id:
            _delete_session(port, session_id)
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
