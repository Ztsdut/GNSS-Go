from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir
from pydantic import BaseModel, Field

from gnssgo.data_networks import default_data_network_registry
from gnssgo.geography import continent_for_country
from gnssgo.models import Station
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.stations.coordinates import normalize_longitude, valid_sirgas_coordinate
from gnssgo.stations.spatial import bbox_filter, radius_filter


def default_catalog_path() -> Path:
    return Path(user_cache_dir("GNSS Go")) / "stations.sqlite"


class CatalogUpdateSummary(BaseModel):
    provider: str | None = None
    source: str | None = None
    fetched: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    final_count: int = 0
    alias_conflicts: list[dict[str, str]] = Field(default_factory=list)
    data_network: str | None = None
    source_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str = "success"
    error: str | None = None


class StationCatalog:
    def __init__(
        self,
        path: Path | str | None = None,
        *,
        seed_if_empty: bool | None = None,
    ) -> None:
        self.path = Path(path) if path else default_catalog_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        should_seed = bool(seed_if_empty) if seed_if_empty is not None else path is not None
        if should_seed and self.count() == 0:
            self.upsert_many(seed_stations(), provider="builtin", source="builtin")

    def _connect(self) -> sqlite3.Connection:
        # Catalog refreshes run in a worker while the GUI reads station counts and
        # filters.  A short busy timeout plus WAL keeps those reads from blocking on
        # a national-network metadata write.
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS stations (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    country TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS station_aliases (
                    alias TEXT PRIMARY KEY,
                    station_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS station_providers (
                    station_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    source TEXT,
                    available_from TEXT,
                    available_to TEXT,
                    last_updated TEXT NOT NULL,
                    PRIMARY KEY (station_id, provider)
                );
                CREATE TABLE IF NOT EXISTS metadata_cache (
                    provider TEXT PRIMARY KEY,
                    source TEXT,
                    last_updated TEXT NOT NULL,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS station_alias_conflicts (
                    alias TEXT NOT NULL,
                    existing_station_id TEXT NOT NULL,
                    incoming_station_id TEXT NOT NULL,
                    provider TEXT,
                    source TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (alias, existing_station_id, incoming_station_id)
                );
                """
            )
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version < 2:
                db.execute("PRAGMA user_version = 2")

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM stations").fetchone()[0])

    def load(self) -> None:
        """Compatibility shim for the previous JSON catalog API."""

    def save(self) -> None:
        """SQLite writes are committed during upsert."""

    def upsert_many(
        self,
        stations: list[Station],
        provider: str | None = None,
        source: str | None = None,
        data_network: str | None = None,
        source_type: str | None = None,
        status: str = "success",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CatalogUpdateSummary:
        summary = CatalogUpdateSummary(
            provider=provider,
            source=source,
            fetched=len(stations),
            data_network=data_network,
            source_type=source_type,
            metadata=metadata or {},
            status=status,
            error=error,
        )
        with self._connect() as db:
            if provider and data_network:
                _prune_provider_data_network(
                    db,
                    provider=provider,
                    data_network=data_network,
                    keep_station_ids={station.id.upper() for station in stations},
                )
            for station in stations:
                merged = self._merge_with_existing(db, station, provider=provider)
                existing_payload = db.execute(
                    "SELECT payload FROM stations WHERE id=?",
                    (merged.id.upper(),),
                ).fetchone()
                payload = _station_payload(merged)
                serialized = json.dumps(payload, separators=(",", ":"), default=str)
                db.execute(
                    """
                    INSERT INTO stations(id, payload, latitude, longitude, country, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        payload=excluded.payload,
                        latitude=excluded.latitude,
                        longitude=excluded.longitude,
                        country=excluded.country,
                        updated_at=excluded.updated_at
                    """,
                    (
                        merged.id.upper(),
                        serialized,
                        merged.latitude,
                        merged.longitude,
                        merged.country,
                        datetime.utcnow().isoformat(),
                    ),
                )
                if existing_payload is None:
                    summary.added += 1
                elif existing_payload["payload"] != serialized:
                    summary.updated += 1
                else:
                    summary.skipped += 1
                for alias in _aliases_for(merged):
                    conflict = self._upsert_alias(
                        db,
                        alias.upper(),
                        merged.id.upper(),
                        provider=provider,
                        source=source,
                    )
                    if conflict:
                        summary.alias_conflicts.append(conflict)
                provider_names = set(merged.providers + ([provider] if provider else []))
                for provider_name in sorted(provider_names):
                    db.execute(
                        """
                        INSERT INTO station_providers(
                            station_id, provider, source, last_updated
                        )
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(station_id, provider) DO UPDATE SET
                            source=excluded.source,
                            last_updated=excluded.last_updated
                        """,
                        (
                            merged.id.upper(),
                            provider_name.lower(),
                            source,
                            datetime.utcnow().isoformat(),
                        ),
                )
            if provider:
                db.execute(
                    """
                    INSERT INTO metadata_cache(provider, source, last_updated, payload)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(provider) DO UPDATE SET
                        source=excluded.source,
                        last_updated=excluded.last_updated,
                        payload=excluded.payload
                    """,
                    (
                        provider.lower(),
                        source,
                        datetime.utcnow().isoformat(),
                        json.dumps(
                            {
                                "provider": provider.lower(),
                                "data_network": data_network,
                                "updated_at": datetime.utcnow().isoformat(),
                                "source_type": source_type,
                                "station_count": len(stations),
                                "status": status,
                                "error": error,
                                **(metadata or {}),
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
        summary.final_count = self.count()
        return summary

    def record_metadata_status(
        self,
        *,
        provider: str,
        source: str | None = None,
        data_network: str | None = None,
        source_type: str | None = None,
        status: str = "failed",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record catalog refresh state without touching cached stations.

        A regional metadata endpoint can fail transiently or return an unexpected
        empty response.  Those conditions must not prune a previously valid station
        catalog.  This helper updates only ``metadata_cache`` so the GUI can report
        the failure while continuing to use the last good station set.
        """
        now = datetime.utcnow().isoformat()
        payload = {
            "provider": provider.lower(),
            "data_network": data_network,
            "updated_at": now,
            "source_type": source_type,
            "station_count": int((metadata or {}).get("station_count") or 0),
            "status": status,
            "error": error,
            **(metadata or {}),
        }
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO metadata_cache(provider, source, last_updated, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    source=excluded.source,
                    last_updated=excluded.last_updated,
                    payload=excluded.payload
                """,
                (
                    provider.lower(),
                    source,
                    now,
                    json.dumps(payload, separators=(",", ":")),
                ),
            )

    def all(self) -> list[Station]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM stations ORDER BY id").fetchall()
        return [
            _station_with_inferred_data_networks(
                Station.model_validate_json(row["payload"])
            )
            for row in rows
        ]

    def get(self, code: str) -> Station | None:
        key = code.upper()
        with self._connect() as db:
            row = db.execute("SELECT payload FROM stations WHERE id=?", (key,)).fetchone()
            if not row:
                alias = db.execute(
                    "SELECT station_id FROM station_aliases WHERE alias=?",
                    (key,),
                ).fetchone()
                if alias:
                    row = db.execute(
                        "SELECT payload FROM stations WHERE id=?",
                        (alias["station_id"],),
                    ).fetchone()
        if not row:
            return None
        return _station_with_inferred_data_networks(
            Station.model_validate_json(row["payload"])
        )

    def search(
        self,
        query: str | None = None,
        network: list[str] | None = None,
        data_networks: list[str] | None = None,
        regional_sources: list[str] | None = None,
        country: str | None = None,
        provider: str | None = None,
        continents: list[str] | None = None,
    ) -> list[Station]:
        # Use the persisted Data Network/country fields to reduce the candidate
        # rows before Pydantic/JSON deserialization.  The old implementation
        # materialized the entire global catalog even when the map showed only
        # Europe, which made every checkbox/search interaction noticeably pause.
        candidate_networks = data_networks
        if continents and data_networks is not None and "igs" not in {str(x).lower() for x in data_networks}:
            candidate_networks = [*data_networks, "igs"]
        stations = self._candidate_stations(candidate_networks)
        if query:
            needle = query.upper()
            stations = [
                station
                for station in stations
                if needle in station.id.upper()
                or any(needle in alias.upper() for alias in station.aliases)
                or needle in (station.marker_name or "").upper()
            ]
        if network:
            wanted = {item.lower() for item in network}
            stations = [
                station
                for station in stations
                if wanted.intersection({item.lower() for item in station.network})
            ]
        if data_networks is not None:
            stations = _filter_data_networks(stations, data_networks, continents=continents)
        if regional_sources is not None:
            stations = _filter_regional_sources(
                stations,
                data_networks=data_networks,
                regional_sources=regional_sources,
                continents=continents,
            )
        if country:
            wanted_country = country.upper()
            stations = [
                station
                for station in stations
                if (station.country or "").upper() == wanted_country
            ]
        if provider:
            wanted_provider = provider.lower()
            stations = [
                station
                for station in stations
                if wanted_provider in {item.lower() for item in station.providers}
            ]
        return stations


    def _candidate_stations(self, data_networks: list[str] | None) -> list[Station]:
        if data_networks is None:
            return self.all()
        selected = {str(item).lower().replace("-", "_") for item in data_networks}
        if not selected:
            return []
        # Candidate rows must include IGS as well as regional CORS when both
        # are selected.  Spatial scoping happens later in _filter_data_networks.
        scope = selected
        clauses: list[str] = []
        params: list[str] = []
        source_registry = default_regional_source_registry()
        network_registry = default_data_network_registry()
        for network_id in sorted(scope):
            sub: list[str] = [
                "EXISTS (SELECT 1 FROM json_each(stations.payload, '$.data_networks') j WHERE lower(j.value)=?)"
            ]
            sub_params: list[str] = [network_id]
            if network_id == "igs":
                sub.append("EXISTS (SELECT 1 FROM json_each(stations.payload, '$.network') j WHERE lower(j.value)='igs')")
            try:
                network_obj = network_registry.get(network_id)
                countries = sorted({str(value).upper() for value in (network_obj.countries or [])})
            except Exception:
                countries = []
            if countries:
                placeholders = ",".join("?" for _ in countries)
                sub.append(f"upper(coalesce(stations.country,'')) IN ({placeholders})")
                sub_params.extend(countries)
            source_ids = [source.id for source in source_registry.all(network_id)]
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                sub.append(
                    "EXISTS (SELECT 1 FROM json_each(stations.payload, '$.regional_sources') j "
                    f"WHERE lower(j.value) IN ({placeholders}))"
                )
                sub_params.extend(source_ids)
            clauses.append("(" + " OR ".join(sub) + ")")
            params.extend(sub_params)
        try:
            with self._connect() as db:
                rows = db.execute(
                    "SELECT payload FROM stations WHERE " + " OR ".join(clauses) + " ORDER BY id",
                    params,
                ).fetchall()
            return [
                _station_with_inferred_data_networks(Station.model_validate_json(row["payload"]))
                for row in rows
            ]
        except sqlite3.OperationalError:
            # JSON1-less SQLite: preserve behavior with the legacy full scan.
            return self.all()

    def search_bbox(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        **filters: Any,
    ) -> list[Station]:
        return bbox_filter(self.search(**filters), west, south, east, north)

    def search_radius(
        self,
        latitude: float,
        longitude: float,
        radius_km: float,
        **filters: Any,
    ) -> list[Station]:
        return radius_filter(self.search(**filters), latitude, longitude, radius_km)


    def regional_source_counts(self, source_ids: list[str] | None = None) -> dict[str, int]:
        """Return station counts by regional-source id without materializing Station objects.

        The GUI previously called ``search()`` once for every checkbox.  ``search()``
        deserializes the entire station catalog, so a Europe panel with seven
        sources could scan thousands of JSON records seven times on every UI
        refresh.  SQLite JSON1 can count the persisted source memberships in one
        query and is orders of magnitude cheaper.
        """
        wanted = None
        if source_ids is not None:
            wanted = {
                default_regional_source_registry().normalize(item)
                for item in source_ids
            }
        try:
            with self._connect() as db:
                rows = db.execute(
                    """
                    SELECT lower(j.value) AS source_id, COUNT(DISTINCT stations.id) AS n
                    FROM stations, json_each(stations.payload, '$.regional_sources') AS j
                    GROUP BY lower(j.value)
                    """
                ).fetchall()
            counts = {str(row["source_id"]): int(row["n"]) for row in rows}
        except sqlite3.OperationalError:
            # Extremely old SQLite builds may lack JSON1.  Fall back to one catalog
            # scan, still much cheaper than one scan per source checkbox.
            counts: dict[str, int] = {}
            for station in self.all():
                for source_id in station.regional_sources:
                    key = default_regional_source_registry().normalize(source_id)
                    counts[key] = counts.get(key, 0) + 1
        if wanted is None:
            return counts
        return {source_id: counts.get(source_id, 0) for source_id in wanted}

    def regional_source_mappable_counts(self, source_ids: list[str] | None = None) -> dict[str, int]:
        """Return counts of stations that have valid map coordinates per regional source."""
        wanted = None
        if source_ids is not None:
            wanted = {
                default_regional_source_registry().normalize(item)
                for item in source_ids
            }
        try:
            with self._connect() as db:
                rows = db.execute(
                    """
                    SELECT lower(j.value) AS source_id, COUNT(DISTINCT stations.id) AS n
                    FROM stations, json_each(stations.payload, '$.regional_sources') AS j
                    WHERE stations.latitude IS NOT NULL
                      AND stations.longitude IS NOT NULL
                      AND stations.latitude BETWEEN -90 AND 90
                      AND stations.longitude BETWEEN -180 AND 180
                      AND (
                          lower(j.value) NOT LIKE 'sirgas_%'
                          OR (
                              stations.latitude BETWEEN -60 AND 35
                              AND stations.longitude BETWEEN -120 AND -30
                          )
                      )
                    GROUP BY lower(j.value)
                    """
                ).fetchall()
            counts = {str(row["source_id"]): int(row["n"]) for row in rows}
        except sqlite3.OperationalError:
            counts: dict[str, int] = {}
            for station in self.all():
                if station.latitude is None or station.longitude is None:
                    continue
                if not (-90 <= station.latitude <= 90 and -180 <= station.longitude <= 180):
                    continue
                for source_id in station.regional_sources:
                    key = default_regional_source_registry().normalize(source_id)
                    counts[key] = counts.get(key, 0) + 1
        if wanted is None:
            return counts
        return {source_id: counts.get(source_id, 0) for source_id in wanted}

    def sanitize_sirgas_coordinates(self) -> int:
        """Repair stale SIRGAS map coordinates already stored in SQLite.

        Parser fixes alone are not enough for desktop users because an older
        ``stations.sqlite`` can keep malformed coordinates until the next full
        national-catalog refresh.  Normalize 0..360 longitudes and clear points
        that are unmistakably outside the SIRGAS/Latin-America envelope.  The
        station record itself is preserved so the next provider refresh can
        enrich it with a valid official coordinate.
        """
        try:
            with self._connect() as db:
                rows = db.execute(
                    """
                    SELECT DISTINCT stations.id, stations.payload
                    FROM stations
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(stations.payload, '$.data_networks') j
                        WHERE lower(j.value)='sirgas'
                    )
                    OR EXISTS (
                        SELECT 1 FROM json_each(stations.payload, '$.regional_sources') j
                        WHERE lower(j.value) LIKE 'sirgas_%'
                    )
                    """
                ).fetchall()
        except sqlite3.OperationalError:
            with self._connect() as db:
                rows = db.execute("SELECT id, payload FROM stations").fetchall()

        changed = 0
        updates: list[tuple[str, float | None, float | None, str]] = []
        for row in rows:
            station = Station.model_validate_json(row["payload"])
            is_sirgas = (
                "sirgas" in {item.lower() for item in station.data_networks}
                or any(item.lower().startswith("sirgas_") for item in station.regional_sources)
            )
            if not is_sirgas or station.latitude is None or station.longitude is None:
                continue

            data = station.model_dump()
            latitude: float | None = station.latitude
            longitude: float | None = station.longitude
            if valid_sirgas_coordinate(latitude, longitude, country_strict=False):
                normalized = normalize_longitude(longitude)
                if abs(normalized - float(longitude)) < 1e-12:
                    continue
                longitude = normalized
            else:
                latitude = None
                longitude = None

            data["latitude"] = latitude
            data["longitude"] = longitude
            updated = Station.model_validate(data)
            updates.append((
                json.dumps(_station_payload(updated), separators=(",", ":"), default=str),
                latitude,
                longitude,
                row["id"],
            ))

        if updates:
            with self._connect() as db:
                now = datetime.utcnow().isoformat()
                for payload, latitude, longitude, station_id in updates:
                    db.execute(
                        """
                        UPDATE stations
                        SET payload=?, latitude=?, longitude=?, updated_at=?
                        WHERE id=?
                        """,
                        (payload, latitude, longitude, now, station_id),
                    )
            changed = len(updates)
        return changed


    def data_network_count(self, network_id: str) -> int:
        """Fast count for a Data Network tooltip.

        Explicit persisted memberships are counted through SQLite JSON1.  Country
        membership is included because ``search()`` also infers regional membership
        for IGS stations from the DataNetwork country scope.
        """
        network_id = str(network_id).lower().replace('-', '_')
        try:
            network = default_data_network_registry().get(network_id)
            countries = sorted({str(value).upper() for value in (network.countries or [])})
        except Exception:
            countries = []
        clauses = [
            "EXISTS (SELECT 1 FROM json_each(stations.payload, '$.data_networks') AS j "
            "WHERE lower(j.value)=?)"
        ]
        params: list[str] = [network_id]
        if countries:
            placeholders = ','.join('?' for _ in countries)
            clauses.append(f"upper(coalesce(stations.country,'')) IN ({placeholders})")
            params.extend(countries)
        try:
            with self._connect() as db:
                row = db.execute(
                    f"SELECT COUNT(*) AS n FROM stations WHERE {' OR '.join(clauses)}",
                    params,
                ).fetchone()
            return int(row['n']) if row else 0
        except sqlite3.OperationalError:
            return len(self.search(data_networks=[network_id]))

    def provider_availability(self, station_id: str) -> list[str]:
        station = self.get(station_id)
        return station.providers if station else []

    def metadata_age_days(self, provider: str = "builtin") -> int | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT last_updated FROM metadata_cache WHERE provider=?",
                (provider.lower(),),
            ).fetchone()
        if not row:
            return None
        updated = datetime.fromisoformat(row["last_updated"])
        return (datetime.utcnow() - updated).days

    def metadata_record(self, provider: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT source, last_updated, payload FROM metadata_cache WHERE provider=?",
                (provider.lower(),),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"] or "{}")
        payload.setdefault("source", row["source"])
        payload.setdefault("updated_at", row["last_updated"])
        return payload

    def _merge_with_existing(
        self,
        db: sqlite3.Connection,
        station: Station,
        *,
        provider: str | None = None,
    ) -> Station:
        row = db.execute(
            "SELECT payload FROM stations WHERE id=?",
            (station.id.upper(),),
        ).fetchone()
        existing = Station.model_validate_json(row["payload"]) if row else None
        if not existing and station.domes:
            row = db.execute(
                "SELECT payload FROM stations WHERE json_extract(payload, '$.domes')=?",
                (station.domes,),
            ).fetchone()
            existing = Station.model_validate_json(row["payload"]) if row else None
        if not existing:
            return _normalized_station(station)
        # A refresh from the same sole provider is authoritative for its own
        # station coordinates/metadata.  Earlier regional parsers could ingest
        # malformed coordinates; the old merge policy kept every non-empty old
        # scalar forever, so a corrected parser could never repair those rows.
        # Do not overwrite coordinates when another provider also contributes to
        # the station (e.g. IGS + EPN + RGP); in that case preserve the established
        # shared identity and only union memberships/capabilities.
        provider_id = (provider or "").lower()
        authoritative = bool(
            provider
            and {item.lower() for item in existing.providers} <= {provider_id}
        )

        # Brazil RBMC map coordinates come from the official IBGE RBMC KMZ and
        # are authoritative for plotting.  Historical catalog rows may already
        # contain coordinates contributed by rbmc_br / IGS / older SIRGAS
        # providers.  Those old non-empty values must not block a corrected KMZ
        # coordinate from replacing them.
        authoritative_coordinate_country = {
            "sirgas_rbmc_br": "BRA",
            "rbmc_br": "BRA",
            "ramsac_ar": "ARG",
            "rgna_mx": "MEX",
            "sirgas_cl": "CHL",
            "sirgas_bo": "BOL",
            "sirgas_co": "COL",
            "sirgas_ec": "ECU",
            "sirgas_pe": "PER",
            "sirgas_uy": "URY",
            "sirgas_cr": "CRI",
            "sirgas_pa": "PAN",
            # Ready for additional SIRGAS national layers when exposed in the UI.
            "sirgas_py": "PRY",
            "sirgas_ve": "VEN",
            "sirgas_gy": "GUY",
            "sirgas_sr": "SUR",
        }.get(provider_id)
        coordinate_authoritative = bool(
            authoritative_coordinate_country
            and (station.country or "").upper() == authoritative_coordinate_country
            and station.latitude is not None
            and station.longitude is not None
        )

        return _merge_station(
            existing,
            station,
            incoming_authoritative=authoritative,
            incoming_coordinates_authoritative=coordinate_authoritative,
        )

    def _upsert_alias(
        self,
        db: sqlite3.Connection,
        alias: str,
        station_id: str,
        *,
        provider: str | None,
        source: str | None,
    ) -> dict[str, str] | None:
        row = db.execute(
            "SELECT station_id FROM station_aliases WHERE alias=?",
            (alias,),
        ).fetchone()
        if row and row["station_id"] != station_id:
            db.execute(
                """
                INSERT INTO station_alias_conflicts(
                    alias, existing_station_id, incoming_station_id, provider, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(alias, existing_station_id, incoming_station_id) DO UPDATE SET
                    provider=excluded.provider,
                    source=excluded.source,
                    created_at=excluded.created_at
                """,
                (
                    alias,
                    row["station_id"],
                    station_id,
                    provider.lower() if provider else None,
                    source,
                    datetime.utcnow().isoformat(),
                ),
            )
            return {
                "alias": alias,
                "existing_station_id": row["station_id"],
                "incoming_station_id": station_id,
            }
        db.execute(
            """
            INSERT INTO station_aliases(alias, station_id)
            VALUES (?, ?)
            ON CONFLICT(alias) DO UPDATE SET station_id=excluded.station_id
            """,
            (alias, station_id),
        )
        return None


def _normalized_station(station: Station) -> Station:
    data = station.model_dump()
    data["id"] = station.id.upper()
    data["aliases"] = sorted({alias.upper() for alias in _aliases_for(station)})
    data["network"] = sorted({item.lower() for item in station.network})
    data["data_networks"] = sorted({item.lower() for item in station.data_networks})
    data["regional_sources"] = sorted(
        {
            default_regional_source_registry().normalize(item)
            for item in station.regional_sources
        }
    )
    data["providers"] = sorted({item.lower() for item in station.providers})
    return Station.model_validate(data)


def _merge_station(
    existing: Station,
    incoming: Station,
    *,
    incoming_authoritative: bool = False,
    incoming_coordinates_authoritative: bool = False,
) -> Station:
    data = existing.model_dump()
    incoming_data = incoming.model_dump()
    for key, value in incoming_data.items():
        if value not in (None, [], {}) and data.get(key) in (None, [], {}):
            data[key] = value
    if incoming_authoritative:
        # These fields are direct facts published by the provider catalog and
        # should be repairable on a later successful refresh.
        for key in (
            "marker_name",
            "domes",
            "latitude",
            "longitude",
            "height",
            "country",
        ):
            value = incoming_data.get(key)
            if value not in (None, ""):
                data[key] = value

    if incoming_coordinates_authoritative:
        # For Brazil RBMC the bundled/local official IBGE KMZ is the map
        # coordinate source of truth.  Overwrite stale coordinates even when
        # the existing station row is shared with another provider such as IGS.
        latitude = incoming_data.get("latitude")
        longitude = incoming_data.get("longitude")
        if latitude is not None:
            data["latitude"] = latitude
        if longitude is not None:
            data["longitude"] = longitude

        # Height in the KMZ is optional.  Only replace an existing value when
        # the incoming KMZ actually provides one.
        height = incoming_data.get("height")
        if height is not None:
            data["height"] = height
    data["aliases"] = sorted({*_aliases_for(existing), *_aliases_for(incoming)})
    data["network"] = sorted({*existing.network, *incoming.network})
    data["data_networks"] = sorted({*existing.data_networks, *incoming.data_networks})
    data["regional_sources"] = sorted({*existing.regional_sources, *incoming.regional_sources})
    data["providers"] = sorted({*existing.providers, *incoming.providers})
    data["metadata"] = _merge_metadata(existing.metadata, incoming.metadata)
    return _normalized_station(Station.model_validate(data))


def _station_payload(station: Station) -> dict[str, Any]:
    return _normalized_station(station).model_dump(mode="json")


def _merge_metadata(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "catalog_sources":
            existing_sources = _metadata_list(merged.get(key))
            incoming_sources = _metadata_list(value)
            merged[key] = sorted({*existing_sources, *incoming_sources})
        elif value not in (None, "", [], {}) and merged.get(key) in (None, "", [], {}):
            merged[key] = value
    return merged


def _metadata_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _station_with_inferred_data_networks(station: Station) -> Station:
    networks = set(station.data_networks)
    sources = set(station.regional_sources)
    if "igs" in {item.lower() for item in station.network}:
        networks.add("igs")
    source_registry = default_regional_source_registry()

    # Backward-compatible source inference for catalogs created before a regional
    # source was promoted to an explicit second-level filter.  Provider identity
    # is authoritative enough here: a CACS/CHAIN catalog record came from that
    # source, whereas a country-only IGS record did not.
    provider_ids = {item.lower() for item in station.providers}
    provider_sources: dict[str, list[str]] = {}
    for source in source_registry.all():
        provider_sources.setdefault(source.provider.lower(), []).append(source.id)
    for provider_id in provider_ids:
        mapped = provider_sources.get(provider_id, [])
        # Infer only when the provider identifies exactly one regional source.
        # GA intentionally represents many Australian source networks, so its
        # provider ID alone must never assign all of them to a station.
        if len(mapped) == 1:
            sources.add(mapped[0])

    for source_id in sources:
        try:
            networks.add(source_registry.get(source_id).data_network)
        except Exception:
            continue
    # Keep region membership aligned with the DataNetwork registry.  This is
    # important for IGS stations: when a user selects IGS + Canada (or another
    # country-scoped regional network), the IGS stations inside that region
    # must be identifiable even when they were loaded only from the global IGS
    # catalog.
    station_country = (station.country or "").upper()
    for data_network in default_data_network_registry().regional_networks():
        countries = {str(value).upper() for value in (data_network.countries or [])}
        if station_country and station_country in countries:
            networks.add(data_network.id)

    # Preserve a few legacy/special territory mappings that are intentionally
    # broader than the registry's country list.
    country_map = {
        "JPN": ("japan",),
        "NZL": ("new_zealand",),
        "NLD": ("netherlands",),
        "ITA": ("italy",),
        "BRA": ("brazil",),
        "CAN": ("canada",),
        "GBR": ("united_kingdom",),
        "FRA": ("france",),
        "ESP": ("spain",),
        "HKG": ("hong_kong",),
        "MNG": ("mongolia",),
        "ARG": ("argentina",),
        "ZAF": ("south_africa",),
        "PRT": ("portugal",),
        "KOR": ("korea",),
        "SGP": ("singapore",),
        "USA": ("united_states", "north_america"),
    }
    for network_id in country_map.get(station.country or "", ()):
        networks.add(network_id)
    if not networks:
        return station
    data = station.model_dump()
    data["data_networks"] = sorted(networks)
    data["regional_sources"] = sorted(sources)
    return Station.model_validate(data)


def _filter_data_networks(
    stations: list[Station],
    data_networks: list[str],
    *,
    continents: list[str] | None = None,
) -> list[Station]:
    """Filter global/regional stations with continent-aware IGS semantics.

    - IGS alone: all IGS stations.
    - IGS + a few regional networks: regional CORS plus IGS inside those regions.
    - Select All (IGS + every regional network): all IGS + all regional CORS.
    - A fully selected continent can request IGS stations in that continent even
      where no national regional-CORS source exists.
    """
    selected = {item.lower().replace("-", "_") for item in data_networks}
    if not selected:
        return []
    selected_regions = selected.difference({"igs"})
    regional_ids = {item.id for item in default_data_network_registry().regional_networks()}
    all_networks_selected = "igs" in selected and regional_ids.issubset(selected_regions)
    wanted_continents = {str(x) for x in (continents or [])}

    result: list[Station] = []
    for station in stations:
        station_networks = {item.lower().replace("-", "_") for item in station.data_networks}
        is_igs = "igs" in station_networks or "igs" in {item.lower() for item in station.network}
        region_match = bool(selected_regions.intersection(station_networks))
        igs_continent_match = is_igs and continent_for_country(station.country) in wanted_continents
        all_continents_selected = wanted_continents.issuperset({
            "Africa", "Antarctica", "Asia", "Europe", "Latin America", "North America", "Oceania"
        })
        if is_igs and all_continents_selected and "igs" in selected:
            result.append(station)
            continue

        if not selected_regions:
            # Default IGS with no continent filter means all IGS.  Once a
            # continent is explicitly checked (including Africa/Antarctica),
            # show only the IGS stations inside that continent.
            if "igs" in selected and wanted_continents:
                keep = is_igs and continent_for_country(station.country) in wanted_continents
            else:
                keep = "igs" in selected and is_igs
        elif all_networks_selected:
            keep = region_match or is_igs
        elif "igs" in selected:
            keep = region_match or igs_continent_match
        else:
            keep = region_match or igs_continent_match
        if keep:
            result.append(station)
    return result


def _filter_regional_sources(
    stations: list[Station],
    *,
    data_networks: list[str] | None,
    regional_sources: list[str],
    continents: list[str] | None = None,
) -> list[Station]:
    registry = default_regional_source_registry()
    wanted_sources = set(registry.normalize_many(regional_sources))
    source_networks = {registry.get(source_id).data_network for source_id in wanted_sources}
    if data_networks is not None:
        selected_networks = {item.lower().replace("-", "_") for item in data_networks}
        source_networks.update(
            network_id
            for network_id in selected_networks
            if registry.all(network_id)
        )
        passthrough_networks = selected_networks.difference(source_networks)
        wanted_continents = {str(x) for x in (continents or [])}
        return [
            station
            for station in stations
            if passthrough_networks.intersection(
                {item.lower().replace("-", "_") for item in station.data_networks}
            )
            or wanted_sources.intersection(station.regional_sources)
            or (
                ("igs" in {item.lower() for item in station.network} or "igs" in {item.lower() for item in station.data_networks})
                and continent_for_country(station.country) in wanted_continents
            )
        ]
    return [
        station
        for station in stations
        if wanted_sources.intersection(station.regional_sources)
    ]


def _prune_provider_data_network(
    db: sqlite3.Connection,
    *,
    provider: str,
    data_network: str,
    keep_station_ids: set[str],
) -> None:
    rows = db.execute(
        """
        SELECT stations.id, stations.payload
        FROM stations
        JOIN station_providers ON station_providers.station_id = stations.id
        WHERE station_providers.provider=?
        """,
        (provider.lower(),),
    ).fetchall()
    source_registry = default_regional_source_registry()
    provider_sources = {
        source.id
        for source in source_registry.all(data_network)
        if source.provider.lower() == provider.lower()
    }
    for row in rows:
        station_id = row["id"].upper()
        if station_id in keep_station_ids:
            continue
        station = Station.model_validate_json(row["payload"])
        other_provider_rows = db.execute(
            "SELECT provider FROM station_providers WHERE station_id=? AND provider<>?",
            (station_id, provider.lower()),
        ).fetchall()
        if not other_provider_rows:
            # This row existed only because of the provider being refreshed.  A
            # complete successful catalog no longer contains it, so remove it
            # outright.  This is what clears stale/bogus regional points after a
            # parser fix instead of leaving them on the map forever.
            db.execute("DELETE FROM station_aliases WHERE station_id=?", (station_id,))
            db.execute("DELETE FROM station_providers WHERE station_id=?", (station_id,))
            db.execute("DELETE FROM stations WHERE id=?", (station_id,))
            continue

        networks = list(station.data_networks)
        sources: list[str] = []
        for item in station.regional_sources:
            try:
                source_id = source_registry.get(item).id
            except Exception:
                sources.append(item)
                continue
            if source_id not in provider_sources:
                sources.append(item)
        providers = [item for item in station.providers if item.lower() != provider.lower()]
        # Remove the broad regional membership only when no remaining regional
        # source in that region still supports the station.  Country/IGS inference
        # can add it back later when appropriate.
        if not any(
            source_registry.get(item).data_network == data_network
            for item in sources
            if item in {source.id for source in source_registry.all()}
        ):
            networks = [item for item in networks if item.lower() != data_network]
        data = station.model_dump()
        data["data_networks"] = networks
        data["regional_sources"] = sources
        data["providers"] = providers
        updated = Station.model_validate(data)
        db.execute(
            """
            UPDATE stations
            SET payload=?, updated_at=?
            WHERE id=?
            """,
            (
                json.dumps(_station_payload(updated), separators=(",", ":"), default=str),
                datetime.utcnow().isoformat(),
                station_id,
            ),
        )
        db.execute(
            "DELETE FROM station_providers WHERE station_id=? AND provider=?",
            (station_id, provider.lower()),
        )


def _aliases_for(station: Station) -> set[str]:
    aliases = {station.id.upper(), station.legacy_id.upper(), *[a.upper() for a in station.aliases]}
    if station.marker_name:
        aliases.add(station.marker_name.upper())
    return {alias for alias in aliases if alias}


def seed_stations() -> list[Station]:
    providers = ["whu", "kasi", "esa", "ign", "sopac", "bdsmart", "bkgftp", "bkg"]
    return [
        Station(
            id="WUH200CHN",
            marker_name="WUH2",
            domes="21602M001",
            latitude=30.5317,
            longitude=114.3572,
            height=25.8,
            country="CHN",
            network=["igs"],
            data_networks=["igs"],
            providers=[*providers, "noaa"],
            aliases=["WUHN00CHN", "WUH2"],
            sampling_rates=["30s"],
            rinex_versions=["2", "3"],
            constellations=["G", "R", "E", "C"],
        ),
        Station(
            id="BJFS00CHN",
            marker_name="BJFS",
            domes="21601M001",
            latitude=39.6086,
            longitude=115.8925,
            height=87.0,
            country="CHN",
            network=["igs"],
            data_networks=["igs"],
            providers=providers,
            aliases=["BJFS"],
            sampling_rates=["30s"],
            rinex_versions=["3"],
        ),
        Station(
            id="HKWS00HKG",
            marker_name="HKWS",
            latitude=22.4344,
            longitude=114.3350,
            height=45.0,
            country="HKG",
            network=["igs"],
            data_networks=["igs", "hong_kong"],
            providers=providers,
            aliases=["HKWS"],
            sampling_rates=["30s"],
            rinex_versions=["3"],
        ),
        Station(
            id="TSKB00JPN",
            marker_name="TSKB",
            domes="21730S005",
            latitude=36.1057,
            longitude=140.0875,
            height=67.0,
            country="JPN",
            network=["igs"],
            data_networks=["igs", "japan"],
            providers=["kasi", "esa", "ign", "sopac", "bkgftp", "bkg"],
            aliases=["TSKB"],
            sampling_rates=["30s"],
            rinex_versions=["2", "3"],
        ),
        Station(
            id="AIRA00JPN",
            marker_name="AIRA",
            domes="21742S001",
            latitude=31.8241,
            longitude=130.5997,
            height=314.0,
            country="JPN",
            network=["igs"],
            data_networks=["igs", "japan"],
            providers=["kasi", "esa", "ign", "sopac", "bkgftp", "bkg"],
            aliases=["AIRA"],
            sampling_rates=["30s"],
            rinex_versions=["3"],
        ),
        Station(
            id="MIZU00JPN",
            marker_name="MIZU",
            domes="21702M001",
            latitude=39.1350,
            longitude=141.1328,
            height=116.0,
            country="JPN",
            network=["igs"],
            data_networks=["igs", "japan"],
            providers=["kasi", "esa", "ign", "sopac", "bkgftp", "bkg"],
            aliases=["MIZU"],
            sampling_rates=["30s"],
            rinex_versions=["3"],
        ),
        Station(
            id="HRAO00ZAF",
            marker_name="HRAO",
            domes="30302M004",
            latitude=-25.890106,
            longitude=27.686981,
            height=1414.3,
            country="ZAF",
            network=["igs"],
            data_networks=["igs"],
            providers=providers,
            aliases=["HRAO"],
            sampling_rates=["30s"],
            rinex_versions=["3"],
        ),
        Station(
            id="DAV100ATA",
            marker_name="DAV1",
            domes="66010M001",
            latitude=-68.577325,
            longitude=77.972611,
            height=44.4,
            country="ATA",
            network=["igs"],
            data_networks=["igs"],
            providers=providers,
            aliases=["DAV1"],
            sampling_rates=["30s"],
            rinex_versions=["3"],
        ),
        Station(
            id="GUAM00GUM",
            marker_name="GUAM",
            domes="50501M002",
            latitude=13.5893,
            longitude=144.8683,
            height=201.0,
            country="GUM",
            network=["igs"],
            data_networks=["igs"],
            providers=["kasi", "esa", "ign", "sopac", "bkgftp", "bkg"],
            aliases=["GUAM"],
            sampling_rates=["30s"],
            rinex_versions=["3"],
        ),
        Station(
            id="ABMF00GLP",
            marker_name="ABMF",
            domes="97103M001",
            latitude=16.2623,
            longitude=-61.5275,
            height=25.0,
            country="GLP",
            network=["igs"],
            data_networks=["igs", "france"],
            providers=["sopac", "esa", "ign", "bkgftp", "bkg"],
            aliases=["ABMF"],
            sampling_rates=["30s"],
            rinex_versions=["3"],
        ),
    ]
