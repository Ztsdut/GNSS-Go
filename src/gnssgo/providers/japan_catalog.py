from __future__ import annotations

import csv
import json
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

GSI_TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/cp/{z}/{x}/{y}.geojson"
GSI_OBSERVATION_CODE_URL = "https://terras.gsi.go.jp/observation_code.php"
DEFAULT_ZOOM = 7
# Covers the Japanese main islands plus Okinotorishima, Minamitorishima and the
# Ogasawara/Ryukyu chains used by GEONET.
JAPAN_BOUNDS = (122.0, 20.0, 155.0, 46.5)  # west, south, east, north


def canonical_geonet_id(file_code: str) -> str:
    """Return the 9-character GEONET display/canonical ID used by GNSS Go.

    Terras itself still uses the official Japanese station name / 局番号 for
    browser selection.  The public map/list should use a conventional
    four-character site code plus monument/country suffix, e.g. 085100JPN,
    rather than the older internal JP0851JPN placeholder.
    """
    code = re.sub(r"[^0-9A-Za-z]", "", str(file_code or "").upper())[-4:].zfill(4)
    return f"{code}00JPN"


def legacy_geonet_id(file_code: str) -> str:
    code = re.sub(r"[^0-9A-Za-z]", "", str(file_code or "").upper())[-4:].zfill(4)
    return f"JP{code}JPN"


@dataclass
class JapanStationRecord:
    station_id: str
    station_no: str
    file_code: str
    point_code: str
    name_jp: str
    prefecture: str
    facility: str
    receiver: str
    antenna: str
    latitude: float
    longitude: float
    source_tile: str = ""


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            value = unicodedata.normalize("NFKC", "".join(self._cell)).replace("\u3000", " ")
            value = re.sub(r"\s+", " ", value).strip()
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _fetch_text(url: str, timeout: float = 30.0) -> str:
    req = Request(url, headers={"User-Agent": "GNSS Go/GEONET catalog updater Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() if hasattr(response.headers, "get_content_charset") else None
    if charset:
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    for encoding in ("utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse_observation_code_html(html: str) -> dict[str, dict[str, str]]:
    parser = _TableParser()
    parser.feed(html)
    result: dict[str, dict[str, str]] = {}
    for row in parser.rows:
        if len(row) < 7 or not re.fullmatch(r"\d{5,6}", row[0]):
            continue
        station_no, point_code, name, prefecture, facility, receiver, antenna = row[:7]
        if not point_code:
            continue
        result[point_code] = {
            "station_no": station_no,
            "point_code": point_code,
            "name_jp": name,
            "prefecture": prefecture,
            "facility": facility,
            "receiver": receiver,
            "antenna": antenna,
        }
    return result


def _tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat = max(min(lat, 85.05112878), -85.05112878)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def japan_tiles(z: int = DEFAULT_ZOOM, bounds=JAPAN_BOUNDS) -> list[tuple[int, int]]:
    west, south, east, north = bounds
    x0, y_south = _tile_xy(west, south, z)
    x1, y_north = _tile_xy(east, north, z)
    ymin, ymax = sorted((y_north, y_south))
    return [(x, y) for x in range(min(x0, x1), max(x0, x1) + 1) for y in range(ymin, ymax + 1)]


def _norm_key(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z一-龥ぁ-んァ-ヶ]", "", unicodedata.normalize("NFKC", str(value))).casefold()


def _value_from_properties(props: dict, names: tuple[str, ...]) -> str:
    normalized = {_norm_key(k): v for k, v in props.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if value not in {None, ""}:
            return str(value).strip()
    return ""


def _flatten_strings(value) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_strings(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_strings(v) for v in value)
    return str(value or "")


def parse_cp_geojson(payload: dict, *, source_tile: str = "") -> list[dict]:
    records: list[dict] = []
    for feature in payload.get("features", []) if isinstance(payload, dict) else []:
        if not isinstance(feature, dict):
            continue
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") != "Point" or len(coords) < 2:
            continue
        try:
            lon, lat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            continue
        props = feature.get("properties") or {}
        blob = _flatten_strings(props)
        point_code = _value_from_properties(
            props,
            ("基準点コード", "基準点code", "point_code", "pointcode", "code"),
        )
        if not point_code:
            match = re.search(r"\bEL\d{8,14}\b", blob, flags=re.I)
            point_code = match.group(0).upper() if match else ""
        name = _value_from_properties(
            props,
            ("局名称", "点名", "基準点名", "名称", "name", "title"),
        )
        if not point_code:
            continue
        records.append({
            "point_code": point_code,
            "name_jp": name,
            "latitude": lat,
            "longitude": lon,
            "source_tile": source_tile,
        })
    return records


def fetch_cp_records(*, z: int = DEFAULT_ZOOM, workers: int = 8) -> dict[str, dict]:
    def load(tile: tuple[int, int]):
        x, y = tile
        url = GSI_TILE_URL.format(z=z, x=x, y=y)
        raw = _fetch_text(url, timeout=20.0)
        return url, json.loads(raw)

    merged: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as pool:
        futures = {pool.submit(load, tile): tile for tile in japan_tiles(z) }
        for future in as_completed(futures):
            try:
                url, payload = future.result()
            except Exception:
                # Empty/outside-country tiles and transient individual tile errors
                # must not invalidate the whole national catalog.
                continue
            for record in parse_cp_geojson(payload, source_tile=url):
                merged[record["point_code"]] = record
    return merged


def build_station_records(*, workers: int = 8) -> list[JapanStationRecord]:
    coords = fetch_cp_records(workers=workers)
    obs = parse_observation_code_html(_fetch_text(GSI_OBSERVATION_CODE_URL, timeout=40.0))
    rows: list[JapanStationRecord] = []
    for point_code, coord in coords.items():
        meta = obs.get(point_code)
        if not meta:
            continue
        station_no = meta["station_no"]
        file_code = station_no[-4:].zfill(4)
        rows.append(JapanStationRecord(
            station_id=canonical_geonet_id(file_code),
            station_no=station_no,
            file_code=file_code,
            point_code=point_code,
            name_jp=meta.get("name_jp") or coord.get("name_jp") or station_no,
            prefecture=meta.get("prefecture", ""),
            facility=meta.get("facility", ""),
            receiver=meta.get("receiver", ""),
            antenna=meta.get("antenna", ""),
            latitude=float(coord["latitude"]),
            longitude=float(coord["longitude"]),
            source_tile=coord.get("source_tile", ""),
        ))
    # station number is authoritative and stable for Terras download mapping.
    unique = {row.station_no: row for row in rows}
    return sorted(unique.values(), key=lambda item: (item.prefecture, item.station_no))


_CSV_FIELDS = [
    "station_id", "station_no", "file_code", "point_code", "name_jp", "prefecture",
    "facility", "receiver", "antenna", "latitude", "longitude", "source_tile",
]


def write_station_csv(path: str | Path, rows: list[JapanStationRecord]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return output


def read_station_csv(path: str | Path) -> list[JapanStationRecord]:
    source = Path(path)
    if not source.exists():
        return []
    result: list[JapanStationRecord] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                station_no = str(row.get("station_no") or "").strip()
                raw_file_code = str(row.get("file_code") or "").strip()
                if not raw_file_code and station_no:
                    raw_file_code = station_no[-4:]
                if not raw_file_code:
                    old_id = str(row.get("station_id") or "").strip().upper()
                    match = re.search(r"(?:JP)?([0-9A-Z]{4})(?:00)?JPN$", old_id)
                    raw_file_code = match.group(1) if match else ""
                file_code = raw_file_code.zfill(4)
                result.append(JapanStationRecord(
                    station_id=canonical_geonet_id(file_code),
                    station_no=station_no,
                    file_code=file_code,
                    point_code=str(row.get("point_code") or ""),
                    name_jp=str(row.get("name_jp") or ""),
                    prefecture=str(row.get("prefecture") or ""),
                    facility=str(row.get("facility") or ""),
                    receiver=str(row.get("receiver") or ""),
                    antenna=str(row.get("antenna") or ""),
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    source_tile=str(row.get("source_tile") or ""),
                ))
            except (KeyError, TypeError, ValueError):
                continue
    return result
