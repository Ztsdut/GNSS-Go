# Contributing to GNSS Go

Thanks for contributing. Keep provider-specific URL construction and download behavior in the provider/download layers; the GUI and CLI should call the shared core rather than duplicate transport logic.

## Development setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,hatanaka,unix-z]"
pytest -q -m "not integration"
ruff check src tests
```

Integration tests may contact third-party GNSS services and are not run by default.

## Pull requests

- Keep credentials, cookies, `.env`, local catalogs, downloaded RINEX files, and browser profiles out of commits.
- Add or update tests for provider/resolver changes.
- Do not invent undocumented provider endpoints; mark a source as manual/unverified until its machine access is verified.
- Preserve English/Chinese GUI translations for new user-facing strings.
