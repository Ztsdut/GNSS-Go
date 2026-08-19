from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from ftplib import FTP, error_perm
from datetime import datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

import httpx

from gnssgo.download.chromium import (
    download_with_installed_chromium,
    download_with_user_browser_session,
)
from gnssgo.download.chromedriver import download_with_chromedriver
from gnssgo.download.events import (
    CancellationToken,
    DownloadEvent,
    DownloadEventType,
    EventCallback,
)
from gnssgo.download.retry import retry_after_seconds
from gnssgo.download.validator import partial_file_is_invalid, validate_download_content
from gnssgo.exceptions import AuthenticationError, DownloadError, RemoteFileNotFound
from gnssgo.models import DownloadTask
from gnssgo.network import ProxyConfig


class HttpDownloader:
    def __init__(
        self,
        connect_timeout: float = 20,
        read_timeout: float = 120,
        proxy: str | None = None,
        network_settings=None,
    ) -> None:
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        self.network_settings = network_settings
        self.proxy_config = ProxyConfig.from_settings(
            network_settings,
            legacy_proxy=proxy,
        )
        self.proxy_config.validate()
        http_proxy = self.proxy_config.proxy_url(protocol="https")
        # ``system`` uses Python/OS proxy discovery for HTTP(S).  Direct mode
        # intentionally ignores environment proxies.  Custom HTTP/SOCKS5 mode is
        # passed explicitly so the same GNSS Go setting is used by httpx/curl.
        trust_env = (
            self.proxy_config.mode == "system"
            and self.proxy_config.use_for_http
            and http_proxy is None
        )
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            proxy=http_proxy,
            trust_env=trust_env,
        )
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._primed_public_hosts: set[str] = set()
        self._prime_locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def download(
        self,
        task: DownloadTask,
        resume: bool = True,
        *,
        cancellation_token: CancellationToken | None = None,
        event_callback: EventCallback | None = None,
    ) -> Path:
        token = cancellation_token or CancellationToken()
        token.raise_if_cancelled()
        if str(task.remote.url).lower().startswith("ftp://"):
            return await self._download_ftp(
                task,
                resume=resume,
                cancellation_token=token,
                event_callback=event_callback,
            )
        if str(task.remote.url).lower().startswith("sftp://"):
            return await self._download_sftp(
                task,
                resume=resume,
                cancellation_token=token,
                event_callback=event_callback,
            )

        # Korea GNSS Data Integrated Center.  Its public web page creates a
        # short-lived ZIP key in the current JSESSIONID and immediately consumes
        # that key through getZip.do.  Reproduce the same public web-session
        # workflow directly over HTTP so the user does not need a browser, an
        # OpenAPI credential, or an NGII file-server key.
        if task.remote.metadata.get("http_transport") == "ngii_session_zip":
            return await self._download_ngii_session_zip(
                task,
                cancellation_token=token,
                event_callback=event_callback,
            )

        # Japan GEONET/Terras interactive browser transport.  The official
        # web service requires station/date selection and a bulk-download click,
        # so this route uses the configured standalone ChromeDriver and writes a
        # single GNSS Go bundle directly into the archive workflow.
        if task.remote.metadata.get("http_transport") == "geonet_chromedriver":
            from gnssgo.download.geonet import download_geonet_bundle

            task.destination.parent.mkdir(parents=True, exist_ok=True)
            task.temporary_path.unlink(missing_ok=True)
            driver_path = getattr(self.network_settings, "chromedriver_path", "") if self.network_settings is not None else ""

            def _geonet_progress(downloaded_bytes: int) -> None:
                if event_callback:
                    event_callback(
                        DownloadEvent(
                            type=DownloadEventType.FILE_PROGRESS,
                            remote_file=task.remote,
                            downloaded_bytes=downloaded_bytes,
                            total_bytes=None,
                            metadata={"stage": "geonet_browser_progress", "transport": "geonet_chromedriver"},
                        )
                    )

            sep = "\u241f"
            await asyncio.to_thread(
                download_geonet_bundle,
                str(task.remote.url),
                task.temporary_path,
                station_names=[x for x in task.remote.metadata.get("geonet_station_names", "").split(sep) if x],
                start_date=task.remote.metadata.get("geonet_start", ""),
                end_date=task.remote.metadata.get("geonet_end", ""),
                satellite_choices=[x for x in task.remote.metadata.get("geonet_satellite_choices", "GRJE,GRJ,GR,G").split(",") if x],
                rinex_choices=[x for x in task.remote.metadata.get("geonet_rinex_choices", "3.02,4.01,3.03,2.11").split(",") if x],
                proxy=self.proxy_config,
                chromedriver_path=driver_path or None,
                progress_callback=_geonet_progress,
                cancellation_check=token.raise_if_cancelled,
            )
            validate_download_content(task.temporary_path, remote=task.remote, headers=None)
            return task.temporary_path

        # Chile CSN standalone ChromeDriver transport.  Chrome is configured
        # before navigation so its download manager writes directly into the
        # GNSS Go archive folder; there is no History database polling/import.
        if task.remote.metadata.get("http_transport") == "chromedriver":
            task.destination.parent.mkdir(parents=True, exist_ok=True)
            task.temporary_path.unlink(missing_ok=True)
            driver_path = getattr(self.network_settings, "chromedriver_path", "") if self.network_settings is not None else ""
            def _chromedriver_progress(downloaded_bytes: int) -> None:
                if event_callback:
                    event_callback(
                        DownloadEvent(
                            type=DownloadEventType.FILE_PROGRESS,
                            remote_file=task.remote,
                            downloaded_bytes=downloaded_bytes,
                            total_bytes=None,
                            metadata={
                                "stage": "chromedriver_download_progress",
                                "transport": "chromedriver",
                            },
                        )
                    )

            await asyncio.to_thread(
                download_with_chromedriver,
                str(task.remote.url),
                task.temporary_path,
                expected_filename=task.remote.filename,
                proxy=self.proxy_config,
                # CSN 1 Hz daily files can legitimately take much longer than
                # the generic HTTP read_timeout.  Use a 30-minute absolute cap
                # and declare failure only after 90 seconds with no byte growth.
                timeout=30.0 * 60.0,
                stall_timeout=90.0,
                chromedriver_path=driver_path or None,
                progress_callback=_chromedriver_progress,
                cancellation_check=token.raise_if_cancelled,
            )
            validate_download_content(task.temporary_path, remote=task.remote, headers=None)
            return task.temporary_path

        # Normal-browser import transport retained for compatibility with plans
        # that explicitly request browser History/download-directory discovery.
        # Current Chile CSN plans use the ChromeDriver transport above.
        if task.remote.metadata.get("http_transport") == "user_browser":
            task.destination.parent.mkdir(parents=True, exist_ok=True)
            task.temporary_path.unlink(missing_ok=True)
            await asyncio.to_thread(
                download_with_user_browser_session,
                str(task.remote.url),
                task.temporary_path,
                expected_filename=task.remote.filename,
                timeout=max(float(self.read_timeout), 30.0),
            )
            validate_download_content(task.temporary_path, remote=task.remote, headers=None)
            return task.temporary_path

        # Legacy browser-controlled transport retained for compatibility with any
        # non-CSN provider that may explicitly request it.
        if task.remote.metadata.get("http_transport") == "browser_preferred":
            task.destination.parent.mkdir(parents=True, exist_ok=True)
            task.temporary_path.unlink(missing_ok=True)
            try:
                await asyncio.to_thread(
                    download_with_installed_chromium,
                    str(task.remote.url),
                    task.temporary_path,
                    proxy=self.proxy_config,
                    timeout=max(float(self.read_timeout), 30.0),
                    # Let the installed browser use its native User-Agent.
                    # CSN has already rejected the fresh headless/synthetic-UA
                    # path even when the same direct URL works interactively.
                    user_agent=None,
                )
                validate_download_content(task.temporary_path, remote=task.remote, headers=None)
                return task.temporary_path
            except (RemoteFileNotFound, DownloadError) as exc:
                task.remote.metadata["browser_primary_error"] = str(exc)
                # Only fall back when no Chromium browser is available locally.
                # If the real browser engine itself received an HTTP error, sending
                # another curl/httpx request is both redundant and more likely to
                # trigger CSN throttling.
                low = str(exc).lower()
                if "was not found for csn browser download" not in low:
                    raise

        # A few national CORS archives are reachable in a normal browser but are
        # unreliable with Python/OpenSSL on some Windows installations. Providers
        # can opt into the OS curl transport first; httpx remains the fallback.
        if task.remote.metadata.get("http_transport") == "curl_preferred" or (
            task.remote.metadata.get("http_transport") == "browser_preferred"
            and task.remote.metadata.get("browser_primary_error")
        ):
            try:
                return await self._download_curl(
                    task,
                    resume=resume,
                    cancellation_token=token,
                    event_callback=event_callback,
                )
            except DownloadError as exc:
                task.remote.metadata["curl_primary_error"] = str(exc)
                # CSN is intentionally sent as one browser-like curl GET.  If
                # the server answered with an HTTP error, do not immediately
                # send a second httpx request: that creates duplicate traffic and
                # can turn a recoverable CSN response into 429/503 throttling.
                # Fall back only when curl itself is unavailable locally.
                if task.remote.metadata.get("csn_browser_get") == "1":
                    if "not installed" not in str(exc).lower() and "not on path" not in str(exc).lower():
                        raise

        prime_url = task.remote.metadata.get("http_prime_url")
        if prime_url:
            await self._prime_public_session(
                str(prime_url),
                user_agent=str(task.remote.metadata.get("http_user_agent") or ""),
                referer=str(task.remote.metadata.get("http_prime_referer") or prime_url),
            )
        task.destination.parent.mkdir(parents=True, exist_ok=True)
        if resume and partial_file_is_invalid(task.temporary_path):
            task.temporary_path.unlink(missing_ok=True)
        csn_browser_get = task.remote.metadata.get("csn_browser_get") == "1"
        if csn_browser_get:
            task.temporary_path.unlink(missing_ok=True)
            start = 0
        else:
            start = task.temporary_path.stat().st_size if resume and task.temporary_path.exists() else 0
        headers = {"Range": f"bytes={start}-"} if start else {}
        # Some national GNSS archives serve direct files to ordinary browsers
        # but reject generic Python HTTP clients.  Providers may attach a
        # conservative User-Agent/Referer pair without changing the global
        # behaviour for every data centre.
        user_agent = task.remote.metadata.get("http_user_agent")
        referer = task.remote.metadata.get("http_referer")
        if user_agent:
            headers["User-Agent"] = str(user_agent)
        if referer:
            headers["Referer"] = str(referer)
        headers.setdefault("Accept", "*/*")
        if task.remote.metadata.get("http_prime_url"):
            headers.setdefault("Accept-Language", "en-US,en;q=0.9")
            headers.setdefault("Cache-Control", "no-cache")
            headers.setdefault("Pragma", "no-cache")
            headers.setdefault("Sec-Fetch-Dest", "document")
            headers.setdefault("Sec-Fetch-Mode", "navigate")
            headers.setdefault("Sec-Fetch-Site", "same-origin")
            headers.setdefault("Upgrade-Insecure-Requests", "1")
        auth = self._auth_for(task)

        try:
            started_at = perf_counter()
            csn_strict = task.remote.metadata.get("csn_strict_https") == "1"
            async with self.client.stream(
                "GET",
                str(task.remote.url),
                headers=headers,
                auth=auth,
                follow_redirects=False if csn_strict else True,
            ) as response:
                if csn_strict and response.status_code in {301, 302, 303, 307, 308}:
                    location = str(response.headers.get("location") or "").strip()
                    safe_location = _safe_url(location) if location else "(missing Location)"
                    # Exact CSN file URLs should not need a redirect. CSN may
                    # redirect throttled requests to a synthetic /=503 endpoint;
                    # following it can create an HTTP<->HTTPS loop behind some
                    # system proxies. Stop immediately and preserve the cause.
                    if "=503" in location or "/503" in location or "too" in location.lower():
                        raise DownloadError(
                            "Chile CSN server throttled the file request "
                            f"(HTTP {response.status_code} redirect to {safe_location}). "
                            "Retry later or use a different network/proxy exit."
                        )
                    raise DownloadError(
                        "Unexpected Chile CSN redirect while downloading "
                        f"{_safe_url(str(task.remote.url))}: HTTP {response.status_code} "
                        f"Location={safe_location}. Redirect was not followed to avoid a loop."
                    )
                if response.status_code in {401, 403}:
                    raise AuthenticationError(
                        "Authentication failed while downloading "
                        f"{task.remote.provider.upper()} data."
                    )
                safe_url = _safe_url(str(response.url))
                if response.status_code == 404:
                    raise RemoteFileNotFound(f"Remote file not found: {safe_url}")
                if response.status_code == 429:
                    # Respect server throttling.  CSN in particular may return
                    # 429 when several station files are requested back-to-back.
                    wait = retry_after_seconds(response.headers.get("retry-after"))
                    if wait is None:
                        raw_wait = task.remote.metadata.get("retry_429_seconds")
                        try:
                            wait = float(str(raw_wait)) if raw_wait else 15.0
                        except (TypeError, ValueError):
                            wait = 15.0
                    wait = min(max(wait, 1.0), 120.0)
                    token.raise_if_cancelled()
                    await asyncio.sleep(wait)
                    prime_url = task.remote.metadata.get("http_prime_url")
                    if prime_url:
                        # CSN's throttle endpoint can redirect to a synthetic
                        # ``/=503`` URL. Refresh the ordinary web session/cookie
                        # before retrying the original file URL.
                        await self._prime_public_session(
                            str(prime_url),
                            user_agent=str(task.remote.metadata.get("http_user_agent") or ""),
                            referer=str(task.remote.metadata.get("http_prime_referer") or prime_url),
                            force=True,
                        )
                    display_url = _safe_url(str(task.remote.url))
                    raise DownloadError(
                        f"HTTP 429 rate limited by server while downloading {display_url}; "
                        f"waited {wait:.0f}s before retry"
                    )
                if response.status_code >= 400:
                    raise DownloadError(
                        f"HTTP {response.status_code} while downloading {safe_url}"
                    )
                if event_callback:
                    event_callback(
                        DownloadEvent(
                            type=DownloadEventType.FILE_PROGRESS,
                            remote_file=task.remote,
                            metadata={
                                "stage": "headers_received",
                                "initial_host": urlparse(str(task.remote.url)).hostname or "",
                                "final_host": urlparse(str(response.url)).hostname or "",
                                "http_status": str(response.status_code),
                                "content_type": response.headers.get("content-type", ""),
                                "content_length": response.headers.get("content-length", ""),
                                "accept_ranges": response.headers.get("accept-ranges", ""),
                                "content_disposition": _safe_content_disposition(
                                    response.headers.get("content-disposition", "")
                                ),
                                "ttfb_seconds": f"{perf_counter() - started_at:.3f}",
                            },
                        )
                    )
                resume_offset = start if start and response.status_code == 206 else 0
                if start and response.status_code == 200:
                    task.temporary_path.unlink(missing_ok=True)
                mode = "ab" if resume_offset else "wb"
                with task.temporary_path.open(mode) as handle:
                    async for chunk in response.aiter_bytes():
                        token.raise_if_cancelled()
                        if chunk:
                            handle.write(chunk)
                            if event_callback:
                                event_callback(
                                    DownloadEvent(
                                        type=DownloadEventType.FILE_PROGRESS,
                                        remote_file=task.remote,
                                        downloaded_bytes=task.temporary_path.stat().st_size,
                                        total_bytes=_response_total_bytes(
                                            response.headers,
                                            response.status_code,
                                            resume_offset,
                                        ),
                                        metadata={"stage": "download_progress"},
                                    )
                                )
                validate_download_content(
                    task.temporary_path,
                    remote=task.remote,
                    headers=response.headers,
                    status_code=response.status_code,
                    resume_offset=resume_offset,
                )
        except httpx.TimeoutException as exc:
            timeout_kind = exc.__class__.__name__
            raise DownloadError(
                f"{timeout_kind} while downloading {_safe_url(str(task.remote.url))}"
            ) from exc
        except httpx.HTTPError as exc:
            raise DownloadError(
                f"Network error while downloading {_safe_url(str(task.remote.url))}: {exc}"
            ) from exc
        return task.temporary_path

    async def _download_ngii_session_zip(
        self,
        task: DownloadTask,
        *,
        cancellation_token: CancellationToken,
        event_callback: EventCallback | None = None,
    ) -> Path:
        """Download one Korea GNSSData station/day ZIP through its public session flow.

        ``createToZip.json`` returns an ephemeral integer key.  The key can become
        invalid after it is consumed, so retries must repeat the whole sequence
        rather than retrying a stale ``getZip.do`` URL.
        """
        token = cancellation_token
        token.raise_if_cancelled()
        metadata = task.remote.metadata
        base = str(metadata.get("ngii_base_url") or "https://www.gnssdata.or.kr").rstrip("/")
        portal = f"{base}/download/getDownloadView.do"
        station = str(metadata.get("ngii_station") or (task.remote.station or "")[:4]).upper()
        start_day = str(metadata.get("ngii_start") or (task.remote.date.strftime("%Y%m%d") if task.remote.date else ""))
        end_day = str(metadata.get("ngii_end") or start_day)
        data_type = str(metadata.get("ngii_data_type") or "30")
        manager_code = str(metadata.get("ngii_manager_code") or "RZ")
        if not station or not start_day or not end_day:
            raise DownloadError("Korea GNSSData download is missing station/date metadata.")

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        )
        common_headers = {
            "User-Agent": user_agent,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.6",
        }
        ajax_headers = {
            **common_headers,
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": base,
            "Referer": portal,
            "X-Requested-With": "XMLHttpRequest",
        }

        def emit_stage(stage: str, **extra: str) -> None:
            if event_callback:
                event_callback(
                    DownloadEvent(
                        type=DownloadEventType.FILE_PROGRESS,
                        remote_file=task.remote,
                        metadata={"stage": stage, "transport": "ngii_session_zip", **extra},
                    )
                )

        task.destination.parent.mkdir(parents=True, exist_ok=True)
        task.temporary_path.unlink(missing_ok=True)

        try:
            emit_stage("ngii_session_start", station=station, date=start_day)
            response = await self.client.get(portal, headers=common_headers)
            if response.status_code >= 400:
                raise DownloadError(
                    f"Korea GNSSData session page returned HTTP {response.status_code}."
                )
            token.raise_if_cancelled()

            # The portal records download-purpose statistics before creating the
            # ZIP.  Keep the same values observed in the public browser workflow.
            emit_stage("ngii_usage_log")
            response = await self.client.post(
                f"{base}/poll/add.json",
                headers=ajax_headers,
                data={
                    "purp": "M2",
                    "route": "B1",
                    "uses": "1",
                    "routeReg": "",
                    "purpReg": "",
                    "reg": "",
                },
            )
            if response.status_code >= 400:
                task.remote.metadata["ngii_usage_log_warning"] = f"HTTP {response.status_code}"
            token.raise_if_cancelled()

            emit_stage("ngii_download_log")
            response = await self.client.post(
                f"{base}/downLog/set.json",
                headers=ajax_headers,
                data={"corsId": station, "downId": "", "mngrCde": manager_code},
            )
            if response.status_code >= 400:
                # This endpoint only records the portal download log.  The ZIP
                # creation endpoint below is the authoritative transfer step, so
                # a station-specific logging-code change must not block data.
                task.remote.metadata["ngii_download_log_warning"] = f"HTTP {response.status_code}"
            token.raise_if_cancelled()

            emit_stage("ngii_create_zip")
            reg_dat = datetime.now().strftime("%Y%m%d%H%M%S")
            response = await self.client.post(
                f"{base}/download/createToZip.json",
                headers=ajax_headers,
                data={
                    "corsId": station,
                    "obsStDay": start_day,
                    "obsEdDay": end_day,
                    "regDat": reg_dat,
                    "dataTyp": data_type,
                },
            )
            if response.status_code >= 400:
                raise DownloadError(
                    f"Korea GNSSData ZIP creation returned HTTP {response.status_code}."
                )
            try:
                payload = response.json()
            except Exception as exc:
                raise DownloadError(
                    "Korea GNSSData ZIP creation returned an invalid JSON response."
                ) from exc
            key = payload.get("key") if isinstance(payload, dict) else None
            if not isinstance(payload, dict) or payload.get("result") is not True or key in {None, ""}:
                raise DownloadError(
                    f"Korea GNSSData could not create a ZIP for {station} {start_day}."
                )
            key_text = str(key)
            task.remote.metadata["ngii_last_zip_key"] = key_text
            emit_stage("ngii_zip_created", key=key_text)
            token.raise_if_cancelled()

            # Consume the freshly-created key immediately in the same JSESSIONID.
            zip_url = f"{base}/download/getZip.do?key={key_text}"
            started_at = perf_counter()
            async with self.client.stream(
                "GET",
                zip_url,
                headers={**common_headers, "Accept": "*/*", "Referer": portal},
            ) as zip_response:
                if zip_response.status_code >= 400:
                    raise DownloadError(
                        f"Korea GNSSData ZIP download returned HTTP {zip_response.status_code}."
                    )
                content_type = (zip_response.headers.get("content-type") or "").lower()
                disposition = (zip_response.headers.get("content-disposition") or "").lower()
                if "zip" not in content_type and "attachment" not in disposition:
                    body = await zip_response.aread()
                    text = body.decode("utf-8", errors="ignore")
                    compact = " ".join(text.split())[:240]
                    if "존재하지 않는 zip" in text.lower() or "zip" in compact.lower():
                        reason = "temporary ZIP was unavailable or already expired"
                    else:
                        reason = compact or f"Content-Type={content_type or 'unknown'}"
                    raise DownloadError(
                        f"Korea GNSSData did not return a ZIP for {station} {start_day}: {reason}"
                    )

                total = None
                try:
                    total = int(zip_response.headers.get("content-length") or "")
                except (TypeError, ValueError):
                    total = None
                emit_stage(
                    "ngii_zip_headers",
                    content_type=zip_response.headers.get("content-type", ""),
                    content_disposition=_safe_content_disposition(
                        zip_response.headers.get("content-disposition", "")
                    ),
                    ttfb_seconds=f"{perf_counter() - started_at:.3f}",
                )
                with task.temporary_path.open("wb") as handle:
                    async for chunk in zip_response.aiter_bytes():
                        token.raise_if_cancelled()
                        if not chunk:
                            continue
                        handle.write(chunk)
                        if event_callback:
                            event_callback(
                                DownloadEvent(
                                    type=DownloadEventType.FILE_PROGRESS,
                                    remote_file=task.remote,
                                    downloaded_bytes=task.temporary_path.stat().st_size,
                                    total_bytes=total,
                                    metadata={
                                        "stage": "ngii_zip_download_progress",
                                        "transport": "ngii_session_zip",
                                    },
                                )
                            )
                validate_download_content(
                    task.temporary_path,
                    remote=task.remote,
                    headers=zip_response.headers,
                    status_code=zip_response.status_code,
                    resume_offset=0,
                )
        except httpx.TimeoutException as exc:
            raise DownloadError(
                f"{exc.__class__.__name__} during Korea GNSSData download for {station} {start_day}."
            ) from exc
        except httpx.HTTPError as exc:
            raise DownloadError(
                f"Network error during Korea GNSSData download for {station} {start_day}: {exc}"
            ) from exc

        return task.temporary_path

    async def _download_ftp(
        self,
        task: DownloadTask,
        resume: bool = True,
        *,
        cancellation_token: CancellationToken | None = None,
        event_callback: EventCallback | None = None,
    ) -> Path:
        token = cancellation_token or CancellationToken()
        token.raise_if_cancelled()
        task.destination.parent.mkdir(parents=True, exist_ok=True)
        if self.proxy_config.custom and self.proxy_config.enabled_for("ftp"):
            # ftplib opens a second data socket and has no reliable per-instance
            # HTTP/SOCKS proxy hook. curl handles both FTP control/data channels
            # through the configured proxy, so use it directly in proxy mode.
            return await self._download_curl(
                task,
                resume=resume,
                cancellation_token=token,
                event_callback=event_callback,
            )
        if resume and partial_file_is_invalid(task.temporary_path):
            task.temporary_path.unlink(missing_ok=True)
        start = task.temporary_path.stat().st_size if resume and task.temporary_path.exists() else 0
        primary_error: Exception | None = None
        try:
            await asyncio.to_thread(
                self._download_ftp_sync,
                task,
                start,
                token,
                event_callback,
            )
        except FileNotFoundError as exc:
            # A deterministic FTP URL can still be retried by the generic
            # downloader's provider-level fallback list.  Do not mask a real
            # 550 as a generic transport error.
            raise RemoteFileNotFound(
                f"Remote file not found: {_safe_url(str(task.remote.url))}"
            ) from exc
        except Exception as exc:  # ftplib.error_* are not OSError subclasses
            primary_error = exc

        # Windows ships curl.exe and it often succeeds on university/government
        # FTP servers that are blocked by active/passive mode, proxy, or local
        # firewall policy when accessed through ftplib.  Providers opt into
        # this fallback explicitly so normal FTP behavior is unchanged.
        if primary_error is not None and task.remote.metadata.get("curl_fallback") == "1":
            try:
                return await self._download_curl(
                    task,
                    resume=resume,
                    cancellation_token=token,
                    event_callback=event_callback,
                )
            except DownloadError as curl_exc:
                raise DownloadError(
                    f"FTP failed for {_safe_url(str(task.remote.url))}. "
                    f"ftplib: {primary_error.__class__.__name__}: {primary_error}; "
                    f"curl: {curl_exc}"
                ) from primary_error

        if primary_error is not None:
            if isinstance(primary_error, DownloadError):
                raise primary_error
            raise DownloadError(
                f"FTP error while downloading {_safe_url(str(task.remote.url))}: "
                f"{primary_error.__class__.__name__}: {primary_error}"
            ) from primary_error

        validate_download_content(task.temporary_path, remote=task.remote, headers=None)
        return task.temporary_path

    def _download_ftp_sync(
        self,
        task: DownloadTask,
        start: int,
        token: CancellationToken,
        event_callback: EventCallback | None,
    ) -> None:
        parsed = urlparse(str(task.remote.url))
        if not parsed.hostname or not parsed.path:
            raise DownloadError(f"Invalid FTP URL: {_safe_url(str(task.remote.url))}")

        username = task.remote.metadata.get("ftp_username") or parsed.username or "anonymous"
        password = task.remote.metadata.get("ftp_password") or parsed.password or "anonymous@"
        try:
            port = int(task.remote.metadata.get("ftp_port") or parsed.port or 21)
        except (TypeError, ValueError):
            port = 21

        hosts = _pipe_values(task.remote.metadata.get("ftp_host_candidates"))
        if parsed.hostname not in hosts:
            hosts.insert(0, parsed.hostname)
        paths = _pipe_values(task.remote.metadata.get("ftp_path_candidates"))
        if parsed.path not in paths:
            paths.insert(0, parsed.path)
        expanded_paths: list[str] = []
        for path in paths:
            for candidate in _ftp_path_variants(path):
                if candidate not in expanded_paths:
                    expanded_paths.append(candidate)

        connection_errors: list[str] = []
        not_found_paths: list[str] = []
        for host in hosts:
            try:
                with FTP() as ftp:
                    ftp.connect(host, port=port, timeout=self.connect_timeout)
                    ftp.login(str(username), str(password))
                    for remote_path in expanded_paths:
                        token.raise_if_cancelled()
                        try:
                            total = ftp.size(remote_path)
                        except Exception:
                            total = None
                        # Start each path attempt with a clean temporary file.
                        if start == 0:
                            task.temporary_path.unlink(missing_ok=True)
                        mode = "ab" if start else "wb"
                        downloaded = start
                        try:
                            with task.temporary_path.open(mode) as handle:
                                def write_chunk(chunk: bytes) -> None:
                                    nonlocal downloaded
                                    token.raise_if_cancelled()
                                    handle.write(chunk)
                                    downloaded += len(chunk)
                                    if event_callback:
                                        event_callback(
                                            DownloadEvent(
                                                type=DownloadEventType.FILE_PROGRESS,
                                                remote_file=task.remote,
                                                downloaded_bytes=downloaded,
                                                total_bytes=total,
                                                metadata={
                                                    "stage": "ftp_download_progress",
                                                    "ftp_host": host,
                                                    "ftp_path": remote_path,
                                                },
                                            )
                                        )
                                ftp.retrbinary(
                                    f"RETR {remote_path}",
                                    write_chunk,
                                    rest=start or None,
                                )
                            task.remote.metadata["ftp_host_used"] = host
                            task.remote.metadata["ftp_path_used"] = remote_path
                            return
                        except error_perm as exc:
                            task.temporary_path.unlink(missing_ok=True)
                            if str(exc).startswith("550"):
                                not_found_paths.append(f"{host}:{remote_path}")
                                continue
                            raise
            except Exception as exc:
                connection_errors.append(f"{host}: {exc.__class__.__name__}: {exc}")
                continue

        if not_found_paths and not connection_errors:
            raise FileNotFoundError("; ".join(not_found_paths[:10]))
        details = "; ".join(connection_errors[:6]) or "; ".join(not_found_paths[:10])
        if not_found_paths and connection_errors:
            details = details + "; missing: " + "; ".join(not_found_paths[:4])
        raise DownloadError(details or "FTP connection failed without a server response.")

    async def _download_sftp(
        self,
        task: DownloadTask,
        resume: bool = True,
        *,
        cancellation_token: CancellationToken | None = None,
        event_callback: EventCallback | None = None,
    ) -> Path:
        token = cancellation_token or CancellationToken()
        token.raise_if_cancelled()
        task.destination.parent.mkdir(parents=True, exist_ok=True)
        if resume and partial_file_is_invalid(task.temporary_path):
            task.temporary_path.unlink(missing_ok=True)
        start = task.temporary_path.stat().st_size if resume and task.temporary_path.exists() else 0

        primary_error: Exception | None = None
        try:
            await asyncio.to_thread(
                self._download_sftp_sync, task, start, token, event_callback
            )
        except ImportError as exc:
            primary_error = exc
        except FileNotFoundError as exc:
            # The generic SFTP transport now tries all path variants attached to
            # the RemoteFile in one authenticated session.  If none exists, let
            # the DownloadManager move to the next provider-level fallback.
            raise RemoteFileNotFound(
                f"Remote file not found: {_safe_url(str(task.remote.url))}; {exc}"
            ) from exc
        except Exception as exc:  # Paramiko exceptions are not GNSSGoError/OSError reliably
            primary_error = exc

        # curl builds bundled with Windows commonly do not include the SFTP
        # protocol.  If Paramiko itself is missing, fail with one actionable
        # message instead of hiding the dependency problem behind `curl exit 1`.
        if isinstance(primary_error, ImportError):
            raise DownloadError(
                "SFTP downloads require the 'paramiko' package. "
                "Run: python -m pip install 'paramiko>=3.5'"
            ) from primary_error

        if primary_error is not None and task.remote.metadata.get("curl_fallback") == "1":
            try:
                return await self._download_curl(
                    task,
                    resume=resume,
                    cancellation_token=token,
                    event_callback=event_callback,
                )
            except DownloadError as curl_exc:
                raise DownloadError(
                    f"SFTP failed for {_safe_url(str(task.remote.url))}. "
                    f"Paramiko: {primary_error.__class__.__name__}: {primary_error}; "
                    f"curl: {curl_exc}"
                ) from primary_error
        if primary_error is not None:
            raise DownloadError(
                f"SFTP error while downloading {_safe_url(str(task.remote.url))}: "
                f"{primary_error.__class__.__name__}: {primary_error}"
            ) from primary_error

        validate_download_content(task.temporary_path, remote=task.remote, headers=None)
        return task.temporary_path

    def _download_sftp_sync(
        self,
        task: DownloadTask,
        start: int,
        token: CancellationToken,
        event_callback: EventCallback | None,
    ) -> None:
        try:
            import paramiko
        except ImportError:
            raise

        parsed = urlparse(str(task.remote.url))
        if not parsed.hostname or not parsed.path:
            raise DownloadError(f"Invalid SFTP URL: {_safe_url(str(task.remote.url))}")

        username = task.remote.metadata.get("sftp_username") or parsed.username or "anonymous"
        password = task.remote.metadata.get("sftp_password") or parsed.password or ""
        try:
            port = int(task.remote.metadata.get("sftp_port") or parsed.port or 22)
        except (TypeError, ValueError):
            port = 22

        hosts = _pipe_values(task.remote.metadata.get("sftp_host_candidates"))
        if parsed.hostname not in hosts:
            hosts.insert(0, parsed.hostname)
        paths = _pipe_values(task.remote.metadata.get("sftp_path_candidates"))
        if parsed.path not in paths:
            paths.insert(0, parsed.path)

        connection_errors: list[str] = []
        not_found_paths: list[str] = []
        for host in hosts:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            proxy_sock = None
            try:
                if self.proxy_config.uses_tunnel_proxy("sftp"):
                    proxy_sock = self.proxy_config.open_socket(
                        host,
                        port,
                        float(self.connect_timeout),
                        protocol="sftp",
                    )
                client.connect(
                    host,
                    port=port,
                    username=username,
                    password=password,
                    timeout=self.connect_timeout,
                    banner_timeout=self.connect_timeout,
                    auth_timeout=self.connect_timeout,
                    allow_agent=False,
                    look_for_keys=False,
                    sock=proxy_sock,
                )
                sftp = client.open_sftp()
                try:
                    sftp.get_channel().settimeout(max(10.0, float(self.connect_timeout)))
                except Exception:
                    pass

                # SFTP accounts are frequently chrooted.  Try the provider's
                # absolute path plus its supplied chroot/relative variants before
                # reconnecting.  This is especially important for IGM Uruguay,
                # where FileZilla can display /sftpserver/... while Paramiko may
                # expose the same directory as /YYYY/....
                expanded_paths: list[str] = []
                for remote_path in paths:
                    for candidate in _sftp_path_variants(remote_path):
                        if candidate not in expanded_paths:
                            expanded_paths.append(candidate)

                chosen = None
                total = None
                remote = None
                for remote_path in expanded_paths:
                    token.raise_if_cancelled()
                    try:
                        total = sftp.stat(remote_path).st_size
                        remote = sftp.open(remote_path, "rb")
                        chosen = remote_path
                        break
                    except (OSError, IOError):
                        not_found_paths.append(f"{host}:{remote_path}")
                        continue
                if remote is None or chosen is None:
                    continue

                with remote:
                    if start:
                        remote.seek(start)
                    mode = "ab" if start else "wb"
                    downloaded = start
                    with task.temporary_path.open(mode) as handle:
                        while True:
                            token.raise_if_cancelled()
                            chunk = remote.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            downloaded += len(chunk)
                            if event_callback:
                                event_callback(
                                    DownloadEvent(
                                        type=DownloadEventType.FILE_PROGRESS,
                                        remote_file=task.remote,
                                        downloaded_bytes=downloaded,
                                        total_bytes=total,
                                        metadata={
                                            "stage": "sftp_download_progress",
                                            "sftp_host": host,
                                            "sftp_path": chosen,
                                        },
                                    )
                                )
                task.remote.metadata["sftp_path_used"] = chosen
                task.remote.metadata["sftp_host_used"] = host
                return
            except Exception as exc:
                # Authentication/handshake/DNS errors on one host must not stop
                # an alternate hostname/IP candidate from being attempted.
                connection_errors.append(f"{host}: {exc.__class__.__name__}: {exc}")
            finally:
                try:
                    client.close()
                except Exception:
                    pass
                if proxy_sock is not None:
                    try:
                        proxy_sock.close()
                    except Exception:
                        pass

        if not_found_paths and not connection_errors:
            preview = "; ".join(not_found_paths[:8])
            raise FileNotFoundError(f"tried SFTP paths: {preview}")
        details = "; ".join(connection_errors[:6]) or "; ".join(not_found_paths[:8])
        raise DownloadError(details or "SFTP connection failed without a server response.")

    async def _download_curl(
        self,
        task: DownloadTask,
        resume: bool = True,
        *,
        cancellation_token: CancellationToken | None = None,
        event_callback: EventCallback | None = None,
    ) -> Path:
        token = cancellation_token or CancellationToken()
        token.raise_if_cancelled()
        task.destination.parent.mkdir(parents=True, exist_ok=True)
        if resume and partial_file_is_invalid(task.temporary_path):
            task.temporary_path.unlink(missing_ok=True)
        start = task.temporary_path.stat().st_size if resume and task.temporary_path.exists() else 0
        await asyncio.to_thread(
            self._download_curl_sync, task, start, token, event_callback
        )
        validate_download_content(task.temporary_path, remote=task.remote, headers=None)
        return task.temporary_path

    def _download_curl_sync(
        self,
        task: DownloadTask,
        start: int,
        token: CancellationToken,
        event_callback: EventCallback | None,
    ) -> None:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            raise DownloadError("curl is not installed or not on PATH.")

        url = str(task.remote.url)
        csn_browser_get = task.remote.metadata.get("csn_browser_get") == "1"
        command = [
            curl,
            "--silent",
            "--show-error",
            "--connect-timeout",
            str(max(3, int(self.connect_timeout))),
        ]
        if csn_browser_get:
            # Match a direct browser file navigation as closely as practical:
            # one HTTPS GET, HTTP/1.1, no redirect chase and no automatic retry.
            # Capture the status code ourselves so 404/429/503 are reported
            # accurately instead of being followed into CSN error endpoints.
            command.extend([
                "--http1.1",
                "--write-out",
                "\n__GNSSGO_HTTP_CODE__:%{http_code}\n",
                "--output",
                str(task.temporary_path),
            ])
        else:
            command.extend([
                "--location",
                "--fail",
                "--retry",
                "2",
                "--retry-delay",
                "1",
                "--output",
                str(task.temporary_path),
            ])
        if start and not csn_browser_get:
            command.extend(["--continue-at", "-"])
        if task.remote.metadata.get("curl_insecure") == "1":
            command.append("--insecure")
        user_agent = task.remote.metadata.get("http_user_agent")
        if user_agent:
            command.extend(["--user-agent", str(user_agent)])
        if csn_browser_get:
            command.extend(["--header", "Accept: */*"])
        referer = task.remote.metadata.get("http_referer")
        if referer:
            command.extend(["--referer", str(referer)])

        parsed = urlparse(url)
        proxy_url = self.proxy_config.proxy_url(
            for_curl=True,
            protocol=parsed.scheme.lower(),
        )
        if proxy_url:
            command.extend(["--proxy", proxy_url])
        if parsed.scheme.lower() == "sftp":
            username = task.remote.metadata.get("sftp_username") or parsed.username or "anonymous"
            password = task.remote.metadata.get("sftp_password") or parsed.password or ""
            command.extend(["--user", f"{username}:{password}"])
        elif parsed.scheme.lower() == "ftp":
            username = task.remote.metadata.get("ftp_username") or parsed.username or "anonymous"
            password = task.remote.metadata.get("ftp_password") or parsed.password or "anonymous@"
            command.extend(["--user", f"{username}:{password}"])
        command.append(url)

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE if csn_browser_get else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        while process.poll() is None:
            if token.cancelled:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                token.raise_if_cancelled()
            if event_callback and task.temporary_path.exists():
                event_callback(
                    DownloadEvent(
                        type=DownloadEventType.FILE_PROGRESS,
                        remote_file=task.remote,
                        downloaded_bytes=task.temporary_path.stat().st_size,
                        metadata={"stage": "curl_download_progress"},
                    )
                )
            time.sleep(0.2)
        stdout = (process.stdout.read() if process.stdout is not None else "").strip()
        stderr = (process.stderr.read() if process.stderr is not None else "").strip()
        if csn_browser_get:
            import re
            match = re.search(r"__GNSSGO_HTTP_CODE__:(\d{3})", stdout)
            status = int(match.group(1)) if match else 0
            if status >= 400:
                task.temporary_path.unlink(missing_ok=True)
                safe = _safe_url(url)
                if status == 404:
                    raise RemoteFileNotFound(f"Remote file not found: {safe}")
                if status == 429:
                    raise DownloadError(
                        f"Chile CSN returned HTTP 429 for {safe}. "
                        "The server is rate limiting this request; no automatic retry was sent."
                    )
                if status == 503:
                    raise DownloadError(
                        f"Chile CSN returned HTTP 503 for {safe}. "
                        "The file request reached CSN but the service refused it."
                    )
                raise DownloadError(f"HTTP {status} while downloading {safe}")
            if process.returncode != 0:
                task.temporary_path.unlink(missing_ok=True)
                raise DownloadError(
                    f"curl exit {process.returncode} for {_safe_url(url)}"
                    + (f": {stderr[-600:]}" if stderr else "")
                )
            if status not in {200, 206}:
                task.temporary_path.unlink(missing_ok=True)
                raise DownloadError(
                    f"Chile CSN curl completed without a valid HTTP status for {_safe_url(url)} "
                    f"(status={status or 'unknown'})."
                )
        elif process.returncode != 0:
            raise DownloadError(
                f"curl exit {process.returncode} for {_safe_url(url)}"
                + (f": {stderr[-600:]}" if stderr else "")
            )

    def _auth_for(self, task: DownloadTask) -> httpx.BasicAuth | None:
        return None

    async def _prime_public_session(
        self,
        url: str,
        *,
        user_agent: str = "",
        referer: str = "",
        force: bool = False,
    ) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            return
        if host in self._primed_public_hosts and not force:
            return
        lock = self._prime_locks.setdefault(host, asyncio.Lock())
        async with lock:
            if host in self._primed_public_hosts and not force:
                return
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
            if user_agent:
                headers["User-Agent"] = user_agent
            if referer:
                headers["Referer"] = referer
            try:
                # Priming is opportunistic: it establishes the ordinary web
                # session/cookies used by CSN without turning a transient home
                # page problem into a hard data-download failure.
                response = await self.client.get(url, headers=headers)
                if response.status_code < 500:
                    self._primed_public_hosts.add(host)
            except httpx.HTTPError:
                return

def _pipe_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split("|") if item.strip()]


def _ftp_path_variants(path: str) -> list[str]:
    """Return authenticated/chroot variants for legacy national FTP paths."""
    raw = str(path or "").strip()
    if not raw:
        return []
    values = [raw]
    if raw.startswith("/home/rgna/"):
        tail = raw[len("/home/rgna/"):]
        values.extend([f"/{tail}", tail, f"/RGNA/{tail}", f"/rgna/{tail}"])
    elif raw.startswith("/"):
        values.append(raw.lstrip("/"))
    else:
        values.append("/" + raw)
    unique: list[str] = []
    for item in values:
        if item and item not in unique:
            unique.append(item)
    return unique


def _sftp_path_variants(path: str) -> list[str]:
    """Return safe absolute/relative/chroot variants for a public SFTP path."""
    raw = str(path or "").strip()
    if not raw:
        return []
    values = [raw]
    if raw.startswith("/sftpserver/"):
        values.append(raw[len("/sftpserver"):])
        values.append(raw.lstrip("/"))
        values.append(raw[len("/sftpserver/"):])
    elif raw.startswith("/home/rgna/"):
        tail = raw[len("/home/rgna/"):]
        values.extend([f"/{tail}", tail, f"/RGNA/{tail}", f"/rgna/{tail}"])
    elif raw.startswith("/"):
        values.append(raw.lstrip("/"))
    else:
        values.append("/" + raw)
    unique: list[str] = []
    for item in values:
        if item and item not in unique:
            unique.append(item)
    return unique

def _response_total_bytes(
    headers: httpx.Headers,
    status_code: int,
    resume_offset: int,
) -> int | None:
    """Return the full remote size for progress, including resumed transfers."""
    content_range = headers.get("content-range", "")
    if status_code == 206 and "/" in content_range:
        total_raw = content_range.rsplit("/", 1)[-1].strip()
        if total_raw.isdigit():
            return int(total_raw)
    length = _int_header(headers.get("content-length"))
    if length is None:
        return None
    return resume_offset + length if status_code == 206 else length


def _int_header(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _safe_content_disposition(value: str) -> str:
    if not value:
        return ""
    return value.split(";", 1)[0]


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url.split("?", 1)[0]
    return parsed._replace(query="", fragment="").geturl()
