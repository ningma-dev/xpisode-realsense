#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --locked ruff check .
uv run --locked ruff format --check .
python_version="$(uv run --locked python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
uv run --locked pyright --pythonversion "$python_version"
uv run --locked pytest
uv build
uv run --locked python scripts/check_release.py
