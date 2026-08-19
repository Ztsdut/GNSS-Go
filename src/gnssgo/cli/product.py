from __future__ import annotations

from pathlib import Path

import typer

from gnssgo.cli.display import print_plan, print_product_explain, print_result_summary
from gnssgo.client import GNSSGo
from gnssgo.products import ProductPresetRegistry

app = typer.Typer(
    help=(
        "Download GNSS precise and auxiliary products. Examples: "
        "gnssgo product --type orbit clock --date 2026-08-01 --tier auto --explain; "
        "gnssgo product --preset ppp --date 2026-08-01 --dry-run."
    ),
    invoke_without_command=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)


@app.callback(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def product(
    ctx: typer.Context,
    extra_types: list[str] | None = typer.Argument(
        None,
        help="Additional product types after --type, e.g. --type orbit clock erp.",
    ),
    product_type: list[str] = typer.Option(
        None,
        "--type",
        help="orbit, clock, erp, bias, ionex, sinex, antex.",
    ),
    preset: str | None = typer.Option(
        None,
        "--preset",
        help="Product preset: ppp or ionosphere.",
    ),
    date: str | None = typer.Option(
        None,
        "--date",
        help="Single product date: YYYY-MM-DD or YYYY-DDD.",
    ),
    start: str | None = typer.Option(None, "--start", help="Start date: YYYY-MM-DD or YYYY-DDD."),
    end: str | None = typer.Option(None, "--end", help="End date: YYYY-MM-DD or YYYY-DDD."),
    provider: str = typer.Option("auto", "--provider", help="Provider name or auto."),
    center: str = typer.Option("auto", "--center", help="Analysis center or auto."),
    tier: str = typer.Option("auto", "--tier", help="final, rapid, ultra, or auto."),
    system: str = typer.Option(
        "auto",
        "--system",
        help="auto, multi, gps, glonass, galileo, beidou, qzss.",
    ),
    sampling: str | None = typer.Option(
        None,
        "--sampling",
        help="Requested product sampling, e.g. 30S, 15M.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o", help="Archive root."),
    workers: int = typer.Option(4, "--workers", help="Parallel downloads."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files."),
    keep_compressed: bool = typer.Option(
        False,
        "--keep-compressed",
        help="Keep compressed originals.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without downloading."),
    explain: bool = typer.Option(False, "--explain", help="Show product resolution trace."),
    show_files: bool = typer.Option(False, "--show-files", help="Show every planned file."),
) -> None:
    types = list(product_type or [])
    parsed_types, parsed_options = _parse_extra_args([*(extra_types or []), *ctx.args])
    types.extend(parsed_types)
    provider = parsed_options.get("provider", provider)
    center = parsed_options.get("center", center)
    tier = parsed_options.get("tier", tier)
    system = parsed_options.get("system", system)
    sampling = parsed_options.get("sampling", sampling)
    date = parsed_options.get("date", date)
    start = parsed_options.get("start", start)
    end = parsed_options.get("end", end)
    output = Path(parsed_options["output"]) if "output" in parsed_options else output
    workers = int(parsed_options.get("workers", workers))
    overwrite = overwrite or parsed_options.get("overwrite") == "true"
    keep_compressed = keep_compressed or parsed_options.get("keep_compressed") == "true"
    dry_run = dry_run or parsed_options.get("dry_run") == "true"
    explain = explain or parsed_options.get("explain") == "true"
    show_files = show_files or parsed_options.get("show_files") == "true"
    if preset:
        try:
            types.extend(item.value for item in ProductPresetRegistry().get(preset).product_types)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--preset") from exc
    types = list(dict.fromkeys(types))
    if not types:
        raise typer.BadParameter("Provide --type or --preset.", param_hint="--type")
    if date:
        start = end = date
    if not start or not end:
        raise typer.BadParameter("Provide --date or both --start and --end.", param_hint="--date")
    client = GNSSGo()
    client.settings.download.workers = workers
    plan = client.plan_products(
        product_types=types,
        start=start,
        end=end,
        provider=provider,
        center=center,
        tier=tier,
        system=system,
        sampling=sampling,
        output=output,
        overwrite=overwrite,
        keep_compressed=keep_compressed,
    )
    print_plan(plan, title="GNSS Go Product Plan", show_files=show_files)
    if explain:
        print_product_explain(plan)
    if dry_run:
        return
    results = client.execute_plan(plan)
    print_result_summary(results)


def _parse_extra_args(tokens: list[str]) -> tuple[list[str], dict[str, str]]:
    options: dict[str, str] = {}
    types: list[str] = []
    value_options = {
        "--date": "date",
        "--start": "start",
        "--end": "end",
        "--provider": "provider",
        "--center": "center",
        "--tier": "tier",
        "--system": "system",
        "--sampling": "sampling",
        "--output": "output",
        "-o": "output",
        "--workers": "workers",
    }
    flag_options = {
        "--overwrite": "overwrite",
        "--keep-compressed": "keep_compressed",
        "--dry-run": "dry_run",
        "--explain": "explain",
        "--show-files": "show_files",
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in value_options and index + 1 < len(tokens):
            options[value_options[token]] = tokens[index + 1]
            index += 2
            continue
        if token in flag_options:
            options[flag_options[token]] = "true"
            index += 1
            continue
        if not token.startswith("-"):
            types.append(token)
        index += 1
    return types, options
