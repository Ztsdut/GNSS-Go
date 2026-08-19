from __future__ import annotations

import typer

from gnssgo.cli import config as config_cmd
from gnssgo.cli import data_network as data_network_cmd
from gnssgo.cli import doctor as doctor_cmd
from gnssgo.cli import nav as nav_cmd
from gnssgo.cli import obs as obs_cmd
from gnssgo.cli import product as product_cmd
from gnssgo.cli import station as station_cmd
from gnssgo.cli.display import console
from gnssgo.exceptions import GNSSGoError
from gnssgo.utils.logging import configure_logging
from gnssgo.version import __version__

app = typer.Typer(
    name="gnssgo",
    help="Multi-source GNSS data acquisition and management toolkit.",
    no_args_is_help=True,
)
app.add_typer(obs_cmd.app, name="obs")
app.add_typer(nav_cmd.app, name="nav")
app.add_typer(product_cmd.app, name="product")
app.add_typer(station_cmd.app, name="station")
app.add_typer(data_network_cmd.app, name="data-network")
app.add_typer(config_cmd.app, name="config")
app.command(name="doctor")(doctor_cmd.doctor)


@app.command(name="gui")
def gui() -> None:
    from gnssgo.gui.app import main as gui_main

    raise typer.Exit(code=gui_main())


def version_callback(value: bool) -> None:
    if value:
        console.print(f"GNSS Go {__version__}")
        raise typer.Exit()


@app.callback()
def callback(
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        help="Show version.",
    ),
    verbose: int = typer.Option(0, "-v", "--verbose", count=True, help="Increase log verbosity."),
) -> None:
    configure_logging(verbose)


def main() -> None:
    try:
        app()
    except GNSSGoError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
