from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()


def print_plan(
    plan,
    title: str = "GNSS Go download plan",
    show_files: bool = False,
    max_files: int = 20,
) -> None:
    table = Table(title=title)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    providers = sorted({remote.provider.upper() for remote in plan.remote_files})
    table.add_row("Provider", ", ".join(providers) or "none")
    stations = sorted({remote.station for remote in plan.remote_files if remote.station})
    if stations:
        table.add_row("Stations", str(len(stations)))
    days = sorted({remote.date for remote in plan.remote_files if remote.date})
    if days:
        table.add_row("Dates", f"{len(days)} days")
    table.add_row("Remote files", str(len(plan.remote_files)))
    if getattr(plan, "matched_stations", None):
        table.add_row("Matched stations", str(len(plan.matched_stations)))
    table.add_row("Existing", str(len(plan.existing_files)))
    table.add_row("To download", str(len(plan.download_tasks)))
    table.add_row("Missing", str(len(plan.missing)))
    if getattr(plan, "unavailable", None):
        table.add_row("Unavailable", str(len(plan.unavailable)))
    if plan.estimated_size is not None:
        table.add_row("Estimated size", f"{plan.estimated_size / 1024 / 1024:.1f} MB")
    console.print(table)
    files = plan.remote_files if show_files else plan.remote_files[:max_files]
    for remote in files:
        console.print(f"{remote.provider.upper():<6} {remote.data_type:<8} {remote.filename}")
    if not show_files and len(plan.remote_files) > max_files:
        console.print(f"... {len(plan.remote_files) - max_files} more files. Use --show-files.")


def print_product_explain(plan) -> None:
    traces: list[str] = []
    warnings: list[str] = []
    for remote in plan.remote_files:
        trace = remote.metadata.get("resolution_trace")
        if trace:
            traces.extend(item.strip() for item in trace.split("|") if item.strip())
        warning = remote.metadata.get("resolution_warnings")
        if warning:
            warnings.extend(item.strip() for item in warning.split("|") if item.strip())
    traces = list(dict.fromkeys(traces))
    warnings = list(dict.fromkeys(warnings))
    if warnings:
        console.print("[yellow]Compatibility warnings[/yellow]")
        for warning in warnings:
            console.print(f"  {warning}")
    if traces:
        console.print("[bold]Resolution trace[/bold]")
        for item in traces:
            console.print(f"  {item}")


def print_result_summary(results) -> None:
    failed_results = [item for item in results if item.status == "failed"]
    skipped = sum(1 for item in results if item.status == "skipped")
    processed = sum(1 for item in results if item.status == "processed")
    validated = sum(1 for item in results if item.status == "validated")
    console.print(
        f"Downloaded {len(results) - len(failed_results) - skipped}; "
        f"Processed {processed}; Validated {validated}; Skipped {skipped}; "
        f"Failed {len(failed_results)}"
    )
    for item in failed_results:
        console.print(f"[red]FAILED[/red] {item.task.remote.filename}: {item.error}")
