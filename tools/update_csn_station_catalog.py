#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

CSN_HOME = "https://gps.csn.uchile.cl/"
CSN_ARCHIVES = (
    "https://gps.csn.uchile.cl/data",
    "http://gps.csn.uchile.cl/data",
)
CSN_STATION_PAGES = (
    "https://evtdb.csn.uchile.cl/station/{code}",
    "https://shakemaps.csn.uchile.cl/station/{code}",
)
EARTHSCOPE_C1 = (
    "https://service.earthscope.org/fdsnws/station/1/query"
    "?net=C1&level=station&format=text&nodata=404"
)
DEFAULT_OUT = (
    Path(__file__).resolve().parents[1]
    / "src" / "gnssgo" / "data" / "csn_chile_stations.csv"
)

HREF_RE = re.compile(r"href\s*=\s*[\"']([^\"']+)[\"']", re.I)
RINEX2_RE = re.compile(
    r"^(?P<code>[A-Za-z0-9]{4})\d{3}[0-9a-x]?\.\d{2}[do]"
    r"(?:\.(?:z|gz|zip))?$",
    re.I,
)
RINEX3_RE = re.compile(
    r"^(?P<code>[A-Za-z0-9]{4})[A-Za-z0-9]{5,}_.+_"
    r"(?:MO|MN)\.(?:crx|rnx)(?:\.gz|\.zip)?$",
    re.I,
)


def request_text(client: httpx.Client, url: str, attempts: int = 2) -> tuple[str, str]:
    last = None
    for attempt in range(attempts):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.text, str(response.url)
        except (httpx.HTTPError, OSError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last}") from last


def directory_station_codes(text: str) -> set[str]:
    codes = set()
    for href in HREF_RE.findall(text):
        name = html.unescape(href).split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if not name or name in {".", ".."}:
            continue
        match = RINEX2_RE.match(name) or RINEX3_RE.match(name)
        if match:
            codes.add(match.group("code").upper())
    return codes


def sampled_days(today: date, lookback_days: int) -> list[date]:
    days = []
    seen = set()
    for offset in range(1, min(15, lookback_days + 1)):
        d = today - timedelta(days=offset)
        if d not in seen:
            days.append(d)
            seen.add(d)
    for offset in range(21, lookback_days + 1, 7):
        d = today - timedelta(days=offset)
        if d not in seen:
            days.append(d)
            seen.add(d)
    return days


def parse_day(value: str) -> date:
    value = value.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{4})[/\-](\d{3})", value)
    if not m:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD or YYYY/DOY")
    year, doy = int(m.group(1)), int(m.group(2))
    if not 1 <= doy <= 366:
        raise argparse.ArgumentTypeError("DOY must be 001..366")
    return date(year, 1, 1) + timedelta(days=doy - 1)


def fetch_archive_codes(
    client: httpx.Client,
    explicit_day: date | None,
    lookback_days: int,
    max_populated_days: int,
) -> tuple[set[str], list[str]]:
    days = [explicit_day] if explicit_day else sampled_days(date.today(), lookback_days)
    all_codes = set()
    sources = []
    populated = 0

    for day in days:
        doy = (day - date(day.year, 1, 1)).days + 1
        day_found = False
        for archive in CSN_ARCHIVES:
            url = f"{archive}/{day.year}/{doy:03d}/"
            try:
                text, final_url = request_text(client, url, attempts=1)
            except Exception:
                continue
            codes = directory_station_codes(text)
            if not codes:
                continue
            before = len(all_codes)
            all_codes.update(codes)
            sources.append(final_url)
            populated += 1
            day_found = True
            print(
                f"[archive] {day.isoformat()} DOY {doy:03d}: "
                f"{len(codes)} stations (+{len(all_codes)-before})"
            )
            break

        if explicit_day and not day_found:
            raise RuntimeError(
                f"No CSN RINEX files were found for {day.isoformat()} (DOY {doy:03d})."
            )
        if populated >= max_populated_days:
            break

    if not all_codes:
        raise RuntimeError(
            "No GNSS station codes were discovered from the official CSN archive."
        )
    return all_codes, sources


def strip_tags(raw: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_csn_station_page(raw: str, code: str):
    plain = strip_tags(raw)
    mlat = re.search(r"Latitud\s*:\s*([-+]?\d+(?:[.,]\d+)?)", plain, re.I)
    mlon = re.search(r"Longitud\s*:\s*([-+]?\d+(?:[.,]\d+)?)", plain, re.I)
    if not mlat or not mlon:
        return None

    lat = float(mlat.group(1).replace(",", "."))
    lon = float(mlon.group(1).replace(",", "."))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    elev = None
    melev = re.search(r"Elevaci[oó]n\s*:\s*([-+]?\d+(?:[.,]\d+)?)", plain, re.I)
    if melev:
        try:
            elev = float(melev.group(1).replace(",", "."))
        except ValueError:
            pass

    name = code
    mname = re.search(
        rf"Estaci[oó]n\s+{re.escape(code)}\s*\((.*?)\)",
        plain,
        re.I,
    )
    if mname and mname.group(1).strip():
        name = mname.group(1).strip()

    return {
        "code": code,
        "name": name,
        "latitude": lat,
        "longitude": lon,
        "height_m": elev,
    }


def fetch_csn_coordinates(client: httpx.Client, codes: set[str]):
    lookup = {}
    for idx, code in enumerate(sorted(codes), 1):
        for template in CSN_STATION_PAGES:
            url = template.format(code=code)
            try:
                text, final_url = request_text(client, url, attempts=1)
            except Exception:
                continue
            row = parse_csn_station_page(text, code)
            if row:
                row["coordinate_source"] = final_url
                lookup[code] = row
                break
        if idx % 20 == 0 or idx == len(codes):
            print(f"[CSN metadata] {idx}/{len(codes)} checked; {len(lookup)} mapped")
    return lookup


def fetch_earthscope_c1(client: httpx.Client):
    try:
        text, final_url = request_text(client, EARTHSCOPE_C1, attempts=1)
    except Exception as exc:
        print(f"[EarthScope C1] unavailable: {exc}")
        return {}

    lookup = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 6:
            continue
        try:
            lat = float(parts[2])
            lon = float(parts[3])
            elev = float(parts[4]) if parts[4] else None
        except (ValueError, IndexError):
            continue
        code = parts[1].upper()
        lookup[code] = {
            "code": code,
            "name": parts[5] or code,
            "latitude": lat,
            "longitude": lon,
            "height_m": elev,
            "coordinate_source": final_url,
        }
    print(f"[EarthScope C1] {len(lookup)} metadata rows")
    return lookup


def build_rows(codes, csn, earthscope):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    unresolved = []

    for code in sorted(codes):
        csn_row = csn.get(code)
        es_row = earthscope.get(code)
        chosen = es_row or csn_row
        if not chosen:
            unresolved.append(code)
            continue

        name = str((csn_row or {}).get("name") or chosen.get("name") or code)
        height = chosen.get("height_m")
        rows.append(
            {
                "station_id": f"{code}00CHL",
                "code": code,
                "name": name,
                "latitude": f"{float(chosen['latitude']):.9f}",
                "longitude": f"{float(chosen['longitude']):.9f}",
                "height_m": "" if height is None else f"{float(height):.3f}",
                "downloadable": "1",
                "map_style": "",
                "map_id": "",
                "source_url": str(chosen.get("coordinate_source") or CSN_HOME),
                "updated_utc": now,
            }
        )
    return rows, unresolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--day", type=parse_day, default=None)
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--max-populated-days", type=int, default=24)
    parser.add_argument("--no-earthscope", action="store_true")
    args = parser.parse_args()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.8",
        "Connection": "close",
    }

    with httpx.Client(
        timeout=httpx.Timeout(12.0, connect=6.0, read=10.0),
        follow_redirects=True,
        headers=headers,
        trust_env=True,
    ) as client:
        codes, archive_sources = fetch_archive_codes(
            client,
            args.day,
            max(1, args.lookback_days),
            max(1, args.max_populated_days),
        )
        print(f"CSN GNSS codes   : {len(codes)}")

        csn = fetch_csn_coordinates(client, codes)
        earthscope = {} if args.no_earthscope else fetch_earthscope_c1(client)
        rows, unresolved = build_rows(codes, csn, earthscope)

    if not rows:
        raise RuntimeError(
            "Station codes were found, but no coordinates could be resolved. "
            "The existing CSV was not overwritten."
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "station_id", "code", "name", "latitude", "longitude", "height_m",
        "downloadable", "map_style", "map_id", "source_url", "updated_utc",
    ]
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(args.output)

    unresolved_path = args.output.with_name("csn_chile_unresolved.txt")
    unresolved_path.write_text(
        "\n".join(unresolved) + ("\n" if unresolved else ""),
        encoding="utf-8",
    )

    print()
    print(f"Stations in CSN archive : {len(codes)}")
    print(f"Stations mapped         : {len(rows)}")
    print(f"Unresolved              : {len(unresolved)}")
    print(f"Archive days sampled    : {len(archive_sources)}")
    print(f"Saved                   : {args.output}")
    if unresolved:
        print(f"Unresolved list         : {unresolved_path}")
    print("Google My Maps is NOT used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
