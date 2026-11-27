# Contributing

Thank you for contributing to **Spectrum for Home Assistant**.

## Local setup

Use `uv` and Python 3.14.7. For local development, `pyproject.toml` points `aiospectrum` to the sibling checkout `../aiospectrum` via `[tool.uv.sources]`. CI installs the published PyPI release with `uv sync --no-sources`.

```sh
uv sync
uv pip check
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=custom_components/spectrum --cov-report=term-missing --cov-fail-under=95
```

To test against the published `aiospectrum` package from PyPI instead of a local sibling checkout:

```sh
uv sync --no-sources
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
3. Verify `ci.yml` passes cleanly on the release commit, including `ruff`, `mypy`, `pytest` (>= 95% coverage), `hassfest`, and `hacs/action` without ignored checks.
4. Tag `vX.Y.Z` and publish a GitHub Release; `.github/workflows/release.yml` validates the tag and attaches a manual-install ZIP and SHA-256 checksum.
