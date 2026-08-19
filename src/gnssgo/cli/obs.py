from __future__ import annotations

from pathlib import Path

import typer

from gnssgo.cli.display import print_plan, print_result_summary
from gnssgo.client import GNSSGo

app = typer.Typer(help="Download RINEX observation files.", invoke_without_command=True)


@app.callback()
def obs(
    regional_source_args: list[str] | None = typer.Argument(
        None,
        help="Additional regional sources after --regional-source.",
    ),
    station: list[str] | None = typer.Option(
        None,
        "--station",
        "-s",
        help="Station code, repeatable.",
    ),
    station_file: Path | None = typer.Option(
        None,
        "--station-file",
        help="Text file with one station per line.",
    ),
    bbox: str | None = typer.Option(
        None,
        "--bbox",
        help="Spatial filter: 'WEST SOUTH EAST NORTH' or WEST,SOUTH,EAST,NORTH.",
    ),
    center: str | None = typer.Option(
        None,
        "--center",
        help="Radius center: 'LAT LON' or LAT,LON.",
    ),
    radius: float | None = typer.Option(None, "--radius", help="Radius in km."),
    network: list[str] | None = typer.Option(None, "--network", help="Network filter."),
    data_network: list[str] | None = typer.Option(
        None,
        "--data-network",
        help="Data network filter, e.g. igs, japan, australia.",
    ),
    regional_source: list[str] | None = typer.Option(
        None,
        "--regional-source",
        help="Regional source filter, e.g. CORSNET-NSW.",
    ),
    country: str | None = typer.Option(None, "--country", help="Country code filter."),
    start: str = typer.Option(..., "--start", help="Start date: YYYY-MM-DD or YYYY-DDD."),
    end: str = typer.Option(..., "--end", help="End date: YYYY-MM-DD or YYYY-DDD."),
    provider: str = typer.Option("auto", "--provider", help="Provider name or auto."),
    sampling: str = typer.Option("30s", "--sampling", help="Sampling interval."),
    rinex: str = typer.Option("auto", "--rinex", help="RINEX version family: auto, 2, 3, 4."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Archive root."),
    workers: int = typer.Option(4, "--workers", help="Parallel downloads."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files."),
    keep_compressed: bool = typer.Option(
        False,
        "--keep-compressed",
        help="Keep compressed originals.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without downloading."),
    show_files: bool = typer.Option(False, "--show-files", help="Show every planned file."),
) -> None:
    client = GNSSGo()
    client.settings.download.workers = workers
    plan = client.plan_observations(
        stations=station,
        start=start,
        end=end,
        provider=provider,
        sampling=sampling,
        rinex=rinex,
        station_file=station_file,
        bbox=_bbox_tuple(bbox),
        center=_center_tuple(center),
        radius=radius,
        network=network,
        data_networks=data_network,
        regional_sources=_combine_sources(regional_source, regional_source_args),
        country=country,
        output=output,
        overwrite=overwrite,
        keep_compressed=keep_compressed,
    )
    print_plan(plan, show_files=show_files)
    if dry_run:
        return
    results = client.execute_plan(plan)
    print_result_summary(results)


def _bbox_tuple(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    numbers = _parse_float_list(value)
    if len(numbers) != 4:
        raise typer.BadParameter("--bbox requires WEST SOUTH EAST NORTH.")
    return (numbers[0], numbers[1], numbers[2], numbers[3])


def _center_tuple(value: str | None) -> tuple[float, float] | None:
    if value is None:
        return None
    numbers = _parse_float_list(value)
    if len(numbers) != 2:
        raise typer.BadParameter("--center requires LAT LON.")
    return (numbers[0], numbers[1])


def _parse_float_list(value: str) -> list[float]:
    parts = value.replace(",", " ").split()
    try:
        return [float(part) for part in parts]
    except ValueError as exc:
        raise typer.BadParameter("Expected numeric coordinates.") from exc


def _combine_sources(
    option_sources: list[str] | None,
    argument_sources: list[str] | None,
) -> list[str] | None:
    values = [*(option_sources or []), *(argument_sources or [])]
    return values or None
