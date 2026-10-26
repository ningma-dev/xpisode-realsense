# Contributing

Clone the repository and install its locked environment:

```bash
uv sync --locked --python 3.12
scripts/check.sh
```

The SDK sources are pinned in `pyproject.toml`; `uv.lock` records the complete resolution. No sibling checkout or local station environment is needed. The default tests use numerical geometry and recorded-shape fixtures; they do not open a camera.

`check.sh` runs Ruff, Pyright, tests, package builds and archive validation. To run one stage, use `uv run ruff check .`, `uv run pyright`, `uv run pytest`, or `uv build`. Keep hardware-specific work inside the adapter and use xpisode's generic device and format APIs.

For a release, update the version and changelog, run the checks, and tag the tested commit `v<version>`. The release workflow builds wheel/source archives and attaches them to a GitHub release. Releases do not upload to PyPI.
