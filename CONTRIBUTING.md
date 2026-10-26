# Contributing

For SDK tests and type checking, your Git credentials must have read access to the private `ningma-dev/xpisode` repository. Clone this adapter and install its locked environment:

```bash
uv sync --locked --python 3.12
scripts/check.sh
```

The SDK sources are pinned in `pyproject.toml`; `uv.lock` records the complete resolution. No sibling checkout or local station environment is needed. The default tests use numerical geometry and recorded-shape fixtures; they do not open a camera.

`check.sh` runs Ruff, Pyright, tests, package builds and archive validation. To run one stage, use `uv run ruff check .`, `uv run pyright`, `uv run pytest`, or `uv build`. Keep hardware-specific work inside the adapter and use xpisode's generic device and format APIs.

## Continuous integration

The automatic **CI** workflow runs lint, builds wheel/source archives and validates their contents on Python 3.10 and 3.12. It does not install the private SDKs or run adapter tests.

Maintainers can run **SDK tests** manually on a reviewed commit. Configure the repository secret `XPISODE_SDK_TOKEN` with a fine-grained GitHub token granting Contents read access to `ningma-dev/xpisode`. The workflow runs the full checks on both Python versions. It does not run on pull requests, upload SDK artifacts or cache private dependencies. A missing token fails with an explicit setup message.

Without core access, contributors can still run the same public checks:

```bash
uvx ruff==0.17.2 check .
uvx ruff==0.17.2 format --check .
uv build
```

## Release

Update the version and changelog, then run the full checks locally or through **SDK tests** on the release commit. Only after they pass, tag that commit `v<version>`. The automatic packaging workflow attaches adapter wheel/source archives to a GitHub release; it does not verify runtime behavior or include the core SDKs. Releases do not upload to PyPI.
