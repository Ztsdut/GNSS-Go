from __future__ import annotations

from ftplib import FTP, error_perm
from urllib.parse import urlparse


def filter_existing_ftp_urls(urls: list[str]) -> list[str]:
    existing = [url for url in urls if ftp_file_size(url) not in {None, 0}]
    return existing or urls


def ftp_file_size(url: str) -> int | None:
    parsed = urlparse(url)
    if parsed.scheme != "ftp" or not parsed.hostname or not parsed.path:
        return None
    directory, filename = parsed.path.rsplit("/", 1)
    try:
        with FTP(parsed.hostname, timeout=20) as ftp:
            ftp.login()
            ftp.cwd(directory)
            ftp.voidcmd("TYPE I")
            return ftp.size(filename)
    except (OSError, error_perm):
        return None


def list_ftp_filenames(directory_url: str) -> list[str]:
    parsed = urlparse(directory_url)
    if parsed.scheme != "ftp" or not parsed.hostname or not parsed.path:
        return []
    try:
        with FTP(parsed.hostname, timeout=20) as ftp:
            ftp.login()
            ftp.cwd(parsed.path)
            names = ftp.nlst()
    except (OSError, error_perm):
        return []
    return [name.rstrip("/").rsplit("/", 1)[-1] for name in names if name not in {".", ".."}]
