from __future__ import annotations

import csv
from pathlib import Path

import typer
from rich.table import Table

from gnssgo.cli.display import console
from gnssgo.data_networks import default_data_network_registry
from gnssgo.providers import default_registry
from gnssgo.providers.base import GNSSProvider
from gnssgo.regional_sources import default_regional_source_registry, source_display_name
from gnssgo.stations import StationCatalog, StationQuery

app = typer.Typer(help="Search and inspect cached station metadata.")


def _render_stations(stations, title: str = "Stations", output: Path | None = None) -> None:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "station_id",
                    "latitude",
                    "longitude",
                    "height",
                    "country",
                    "data_networks",
                    "regional_sources",
                    "network",
                    "providers",
                ],
            )
            writer.writeheader()
            for station in stations:
                writer.writerow(
                    {
                        "station_id": station.id,
                        "latitude": station.latitude,
                        "longitude": station.longitude,
                        "height": station.height,
                        "country": station.country or "",
                        "data_networks": ",".join(station.data_networks),
                        "regional_sources": ",".join(station.regional_sources),
                        "network": ",".join(station.network),
                        "providers": ",".join(station.providers),
                    }
                )
        console.print(f"Wrote {len(stations)} stations to {output}.")
        return

    table = Table(title=title)
    table.add_column("ID")
    table.add_column("LAT", justify="right")
    table.add_column("LON", justify="right")
    table.add_column("COUNTRY")
    table.add_column("DATA NETWORK")
    table.add_column("REGIONAL SOURCE")
    table.add_column("NETWORK")
    table.add_column("PROVIDERS")
    for station in stations:
        table.add_row(
            station.id,
            "" if station.latitude is None else f"{station.latitude:.4f}",
            "" if station.longitude is None else f"{station.longitude:.4f}",
            station.country or "",
            ",".join(station.data_networks),
            ",".join(source_display_name(item) for item in station.regional_sources),
            ",".join(station.network),
            ",".join(station.providers[:4]) + ("..." if len(station.providers) > 4 else ""),
        )
    console.print(table)


@app.command()
def update(
    provider: str = typer.Option("bkg", "--provider", help="Provider to update."),
    data_network: list[str] | None = typer.Option(
        None,
        "--data-network",
        help="Update station metadata for a data network.",
    ),
    regional: bool = typer.Option(
        False,
        "--regional",
        help="Update station metadata for all regional data networks.",
    ),
) -> None:
    catalog = StationCatalog()
    registry = default_registry()
    selected_providers = _providers_for_update(
        registry,
        provider=provider,
        data_network=data_network,
        regional=regional,
    )

    import asyncio

    table = Table(title="Station Catalog Update")
    table.add_column("Provider", no_wrap=True, overflow="crop")
    table.add_column("Status", no_wrap=True, overflow="crop")
    table.add_column("Fetched", justify="right")
    table.add_column("Added", justify="right")
    table.add_column("Updated", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Final", justify="right")
    for selected in selected_providers:
        if not selected.capabilities().station_metadata:
            status = _status_label(
                getattr(selected, "status", "station metadata not automated")
            )
            table.add_row(
                selected.name.upper(),
                status,
                "0",
                "0",
                "0",
                "1",
                "0",
                str(catalog.count()),
            )
            continue
        stations = asyncio.run(selected.fetch_station_catalog())
        source = (
            getattr(selected, "station_catalog_source", None)
            or getattr(selected, "portal_url", None)
            or selected.name
        )
        summary = catalog.upsert_many(
            stations,
            provider=selected.name,
            source=source,
            data_network=getattr(selected, "data_network", None),
            source_type=getattr(selected, "source_type", None),
            metadata=getattr(selected, "last_station_catalog_stats", {}),
        )
        table.add_row(
            selected.name.upper(),
            "updated",
            str(summary.fetched),
            str(summary.added),
            str(summary.updated),
            str(summary.skipped),
            str(summary.failed),
            str(summary.final_count),
        )
    table.caption = f"Catalog: {catalog.path}"
    console.print(table)
    _print_australia_update_stats(selected_providers)


@app.command()
def search(query: str) -> None:
    stations = StationQuery(StationCatalog()).search(query)
    _render_stations(stations, title="Station Search")


@app.command("list")
def list_stations(
    regional_source_args: list[str] | None = typer.Argument(
        None,
        help="Additional regional sources after --regional-source.",
    ),
    network: list[str] | None = typer.Option(None, "--network", help="Network filter."),
    country: str | None = typer.Option(None, "--country", help="Country code filter."),
    data_network: list[str] | None = typer.Option(
        None,
        "--data-network",
        help="Data network filter.",
    ),
    regional_source: list[str] | None = typer.Option(
        None,
        "--regional-source",
        help="Regional source filter, e.g. CORSNET-NSW.",
    ),
    provider: str | None = typer.Option(None, "--provider", help="Provider filter."),
) -> None:
    catalog = StationCatalog()
    data_network, regional_source = _resolve_regional_filters(
        data_network,
        _combine_sources(regional_source, regional_source_args),
    )
    stations = catalog.search(
        network=network,
        data_networks=data_network,
        regional_sources=regional_source,
        country=country,
        provider=provider,
    )
    if catalog.count() == 0:
        console.print("Station catalog is empty. Run: gnssgo station update --provider bkg")
        return
    _render_stations(stations, title="Station List")


@app.command()
def bbox(
    regional_source_args: list[str] | None = typer.Argument(
        None,
        help="Additional regional sources after --regional-source.",
    ),
    west: float = typer.Option(..., "--west", help="Western longitude."),
    south: float = typer.Option(..., "--south", help="Southern latitude."),
    east: float = typer.Option(..., "--east", help="Eastern longitude."),
    north: float = typer.Option(..., "--north", help="Northern latitude."),
    network: list[str] | None = typer.Option(None, "--network", help="Network filter."),
    country: str | None = typer.Option(None, "--country", help="Country code filter."),
    data_network: list[str] | None = typer.Option(
        None,
        "--data-network",
        help="Data network filter.",
    ),
    regional_source: list[str] | None = typer.Option(
        None,
        "--regional-source",
        help="Regional source filter, e.g. CORSNET-NSW.",
    ),
    provider: str | None = typer.Option(None, "--provider", help="Provider filter."),
    output: Path | None = typer.Option(None, "--output", help="Write station CSV."),
) -> None:
    data_network, regional_source = _resolve_regional_filters(
        data_network,
        _combine_sources(regional_source, regional_source_args),
    )
    stations = StationCatalog().search_bbox(
        west,
        south,
        east,
        north,
        network=network,
        data_networks=data_network,
        regional_sources=regional_source,
        country=country,
        provider=provider,
    )
    _render_stations(stations, title="Station BBox", output=output)


@app.command()
def radius(
    regional_source_args: list[str] | None = typer.Argument(
        None,
        help="Additional regional sources after --regional-source.",
    ),
    lat: float = typer.Option(..., "--lat", help="Center latitude."),
    lon: float = typer.Option(..., "--lon", help="Center longitude."),
    radius_km: float = typer.Option(..., "--radius", help="Radius in km."),
    network: list[str] | None = typer.Option(None, "--network", help="Network filter."),
    country: str | None = typer.Option(None, "--country", help="Country code filter."),
    data_network: list[str] | None = typer.Option(
        None,
        "--data-network",
        help="Data network filter.",
    ),
    regional_source: list[str] | None = typer.Option(
        None,
        "--regional-source",
        help="Regional source filter, e.g. CORSNET-NSW.",
    ),
    provider: str | None = typer.Option(None, "--provider", help="Provider filter."),
    output: Path | None = typer.Option(None, "--output", help="Write station CSV."),
) -> None:
    data_network, regional_source = _resolve_regional_filters(
        data_network,
        _combine_sources(regional_source, regional_source_args),
    )
    stations = StationCatalog().search_radius(
        lat,
        lon,
        radius_km,
        network=network,
        data_networks=data_network,
        regional_sources=regional_source,
        country=country,
        provider=provider,
    )
    _render_stations(stations, title="Station Radius", output=output)


@app.command()
def info(code: str) -> None:
    station = StationQuery(StationCatalog()).info(code)
    if station is None:
        console.print(f"No cached metadata for {code}.")
        raise typer.Exit(code=1)
    console.print_json(station.model_dump_json())


def _providers_for_update(
    registry,
    *,
    provider: str,
    data_network: list[str] | None,
    regional: bool,
) -> list[GNSSProvider]:
    if regional or data_network:
        network_registry = default_data_network_registry()
        network_ids = list(data_network or [])
        if regional:
            network_ids.extend(network.id for network in network_registry.regional_networks())
        provider_names = network_registry.providers_for(network_ids)
        return [registry.get(name) for name in provider_names]
    return [registry.get(provider)]


def _resolve_regional_filters(
    data_network: list[str] | None,
    regional_source: list[str] | None,
) -> tuple[list[str] | None, list[str] | None]:
    if not regional_source:
        return data_network, None
    source_registry = default_regional_source_registry()
    sources = source_registry.normalize_many(regional_source)
    source_networks = sorted({source_registry.get(item).data_network for item in sources})
    networks = [item.lower().replace("-", "_") for item in (data_network or [])]
    if networks:
        for source_id in sources:
            source = source_registry.get(source_id)
            if source.data_network not in networks:
                raise typer.BadParameter(f"{source.name} is not a source of {', '.join(networks)}.")
        return networks, sources
    return source_networks, sources


def _combine_sources(
    option_sources: list[str] | None,
    argument_sources: list[str] | None,
) -> list[str] | None:
    values = [*(option_sources or []), *(argument_sources or [])]
    return values or None


def _print_australia_update_stats(providers: list[GNSSProvider]) -> None:
    for provider in providers:
        stats = getattr(provider, "last_station_catalog_stats", None)
        if provider.name != "ga" or not stats:
            continue
        summary = Table(title="GA Australia Catalog")
        summary.add_column("Metric")
        summary.add_column("Value", justify="right")
        for label, key in [
            ("Pages fetched", "pages_fetched"),
            ("GA total records fetched", "ga_total_records_fetched"),
            ("GA total unique stations", "ga_total_unique_stations"),
            ("Australia regional records", "australia_regional_records"),
            ("Australia regional unique", "australia_regional_unique"),
            ("Excluded non-Australia", "excluded_non_australia"),
        ]:
            summary.add_row(label, str(stats.get(key, "")))
        console.print(summary)

        sources = Table(title="Australia Sources")
        sources.add_column("Source")
        sources.add_column("Stations", justify="right")
        counts = stats.get("regional_source_counts") or {}
        for source in default_regional_source_registry().all("australia"):
            sources.add_row(source.name, str(counts.get(source.name, 0)))
        console.print(sources)

        excluded_counts = stats.get("excluded_source_counts") or {}
        if excluded_counts:
            excluded = Table(title="Excluded GA Sources")
            excluded.add_column("Source")
            excluded.add_column("Records", justify="right")
            for source, count in excluded_counts.items():
                excluded.add_row(str(source), str(count))
            console.print(excluded)


def _status_label(status: str) -> str:
    return {
        "FULLY_AUTOMATED + LIVE_VERIFIED": "FULL/LIVE",
        "PARTIALLY_AUTOMATED + LIVE_VERIFIED": "PARTIAL/LIVE",
        "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED": "UNVER",
        "BROWSER_REQUIRED": "BROWSE",
        "AUTH_REQUIRED": "AUTH",
        "INTERACTIVE_WEB": "WEB",
    }.get(status, status)
