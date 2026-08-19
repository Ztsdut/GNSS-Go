# Releasing GNSS Go

## 1. Prepare the version

Update both:

- `pyproject.toml` → `project.version`
- `src/gnssgo/version.py` → `__version__`

Then run the unit tests and lint checks.

## 2. Push the repository

```bash
git init
git add .
git commit -m "Initial GNSS Go release"
git branch -M main
git remote add origin <your-github-repository-url>
git push -u origin main
```

## 3. Create a release

```bash
git tag v0.1.2
git push origin v0.1.2
```

The `Build release` GitHub Actions workflow builds on native runners and publishes:

- Windows x64 installer + CLI executable
- macOS DMG + CLI executable
- Linux x86_64 AppImage + CLI executable

No local cross-compilation is required.

## 4. Before making the repository public

Verify that none of these are committed:

- `.env`
- `.gnssgo/`
- browser profiles/cookies/session IDs
- downloaded GNSS data (`data/`)
- `build/`, `dist/`, `.venv/`
