from __future__ import annotations

import re

HREF_RE = re.compile(r"""href=["'](?P<href>[^"']+)["']""", re.IGNORECASE)


def parse_listing_filenames(html: str) -> list[str]:
    names: list[str] = []
    for match in HREF_RE.finditer(html):
        href = match["href"].split("?")[0].rstrip("/")
        if not href or href in {".", ".."}:
            continue
        if "/" in href:
            href = href.rsplit("/", 1)[-1]
        if href not in names:
            names.append(href)
    return names
