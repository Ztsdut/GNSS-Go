from __future__ import annotations

import typer
from rich.table import Table

from gnssgo.cli.display import console
from gnssgo.data_networks import AutomationLevel, default_data_network_registry
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.stations import StationCatalog

app = typer.Typer(help="List and inspect GNSS data networks.")


@app.command("list")
def list_networks() -> None:
    registry = default_data_network_registry()
    table = Table(title="GNSS Go Data Networks")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Category")
    table.add_column("Status")
    for network in registry.all():
        table.add_row(
            network.id,
            network.name,
            network.category,
            _status_label(network.status),
        )
    console.print(table)


@app.command()
def info(network_id: str) -> None:
    network = default_data_network_registry().get(network_id)
    catalog = StationCatalog()
    table = Table(title=f"Data Network: {network.name}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("ID", network.id)
    table.add_row("Name", network.name)
    table.add_row("Category", network.category)
    table.add_row("Providers", ", ".join(network.providers))
    table.add_row("Countries", ", ".join(network.countries or []))
    table.add_row("Sampling", ", ".join(network.sampling))
    table.add_row("Automation", network.automation_level.value)
    table.add_row("Status", _status_label(network.status))
    merged = catalog.search(data_networks=[network.id])
    table.add_row("Merged stations", str(len(merged)))
    official_count = 0
    last_update = ""
    source_type = ""
    provider_status = ""
    for provider in network.providers:
        record = catalog.metadata_record(provider)
        if not record:
            continue
        if record.get("data_network") == network.id:
            official_count += int(record.get("station_count") or 0)
            last_update = str(record.get("updated_at") or "")
            source_type = str(record.get("source_type") or "")
            provider_status = str(record.get("status") or "")
    if official_count or last_update:
        table.add_row("Regional catalog stations", str(official_count))
        table.add_row("Last catalog update", last_update)
        table.add_row("Catalog source type", source_type)
        table.add_row("Catalog status", provider_status)
    if network.id == "australia":
        sources = default_regional_source_registry().all("australia")
        table.add_row("Regional Sources", str(len(sources)))
        for source in sources:
            table.add_row(source.name, str(len(catalog.search(regional_sources=[source.id]))))
    obs_discovery = "LIVE" if network.category == "regional" and official_count else ""
    obs_download = "LIVE" if network.automation_level == AutomationLevel.FULL else ""
    rinex_validation = "LIVE" if network.automation_level == AutomationLevel.FULL else ""
    table.add_row("OBS discovery", obs_discovery)
    table.add_row("OBS download", obs_download)
    table.add_row("RINEX validation", rinex_validation)
    if network.description:
        table.add_row("Description", network.description)
    console.print(table)


def _status_label(status: str) -> str:
    return {
        "FULLY_AUTOMATED + LIVE_VERIFIED": "FULL/LIVE",
        "PARTIALLY_AUTOMATED + LIVE_VERIFIED": "PARTIAL/LIVE",
        "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED": "UNVERIFIED",
        "BROWSER_REQUIRED": "BROWSER",
        "AUTH_REQUIRED": "AUTH",
        "INTERACTIVE_WEB": "WEB",
    }.get(status, status)
