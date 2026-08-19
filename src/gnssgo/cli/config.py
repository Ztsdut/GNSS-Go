from __future__ import annotations

import json

import typer

from gnssgo.cli.display import console
from gnssgo.config import load_settings

app = typer.Typer(help="Inspect GNSS Go configuration.")


@app.command("show")
def show() -> None:
    settings = load_settings()
    console.print_json(json.dumps(settings.model_dump(mode="json"), indent=2))
