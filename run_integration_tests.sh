#!/usr/bin/env bash
set -euo pipefail
uv run pytest src/tests/ -m integration "$@"
