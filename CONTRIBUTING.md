# Contributing

Thank you for contributing to **Spectrum for Home Assistant**.

## Local setup

Use `uv` and Python 3.14.7. The lock pins two mutually exclusive Home Assistant compatibility lanes:

- `minimum` (default): Core `2026.10.0` with `pytest-homeassistant-custom-component==0.13.371`
- `latest`: Core `2026.11.0` with `pytest-homeassistant==2026.11.0`

For local development, `pyproject.toml` points `aiospectrum` to the sibling checkout `../aiospectrum` via `[tool.uv.sources]`. CI installs the published PyPI release with `uv sync --no-sources`.

```sh
uv sync --locked
uv pip check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest --cov=custom_components/spectrum --cov-report=term-missing --cov-fail-under=95
```

To run the `latest` lane in a separate virtual environment:

```sh
export UV_PROJECT_ENVIRONMENT="$PWD/.venv-latest"
uv sync --locked --no-default-groups --group dev --group latest
uv pip check
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest --cov=custom_components/spectrum --cov-report=term-missing --cov-fail-under=95
```

To test against the published `aiospectrum` package from PyPI instead of a local sibling checkout:

```sh
uv sync --no-sources --no-default-groups --group dev --group minimum
```

## Standards

- **Domain language**: Use the canonical terms in [GLOSSARY.md](GLOSSARY.md) across code, entity names, translations, and documentation.
- **Typing & linting**: All modules under `custom_components/spectrum` must pass `mypy` in strict mode and `ruff` (`E`, `F`, `I`, `UP`, `B`, `ASYNC`).
- **Tests**: External network sockets are disabled during pytest (`--disable-socket --allow-unix-socket`). Use synthetic fixtures only—never commit real tokens, account numbers, MAC addresses, serial numbers, WAN IPs, or raw Spectrum captures.
- **No ADRs in git**: Keep `docs/adr/` out of version control (enforced by `.gitignore`).
- **Brand assets**: `custom_components/spectrum/brand/` must contain only Hassfest-allowed PNG files (`icon.png`, `icon@2x.png`, `dark_icon.png`, `dark_icon@2x.png`, `logo.png`, `logo@2x.png`, `dark_logo.png`, `dark_logo@2x.png`). Never place Markdown or SVG files inside `brand/`.
- **Manifest ordering**: Keep `custom_components/spectrum/manifest.json` keys ordered for Hassfest (`domain`, `name`, then alphabetical).

## Release checklist

1. Ensure the matching `aiospectrum` version is published on PyPI before tagging `ha-spectrum`.
2. Keep `pyproject.toml` and `custom_components/spectrum/manifest.json` versions identical.
3. Verify `ci.yml` passes cleanly on the release commit across both `minimum` and `latest` lanes, including `ruff`, `mypy`, `pytest` (>= 95% coverage), `hassfest`, and `hacs/action` without ignored checks.
4. Tag `vX.Y.Z` and publish a GitHub Release; `.github/workflows/release.yml` validates the tag and attaches a manual-install ZIP and SHA-256 checksum.
