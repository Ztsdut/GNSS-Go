from __future__ import annotations

from pathlib import Path

import typer

from gnssgo.cli.display import print_plan, print_result_summary
from gnssgo.client import GNSSGo

app = typer.Typer(help="Download broadcast navigation files.", invoke_without_command=True)


@app.callback()
def nav(
    start: str = typer.Option(..., "--start", help="Start date: YYYY-MM-DD or YYYY-DDD."),
    end: str = typer.Option(..., "--end", help="End date: YYYY-MM-DD or YYYY-DDD."),
    nav_type: str = typer.Option("mixed", "--type", help="mixed, gps, glonass, galileo, beidou."),
    provider: str = typer.Option("auto", "--provider", help="Provider name or auto."),
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
    plan = client.plan_navigation(
        start=start,
        end=end,
        nav_type=nav_type,
        provider=provider,
        output=output,
        overwrite=overwrite,
        keep_compressed=keep_compressed,
    )
    print_plan(plan, show_files=show_files)
    if dry_run:
        return
    results = client.execute_plan(plan)
    print_result_summary(results)
