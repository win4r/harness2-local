#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required; install it first, then rerun this script." >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  uv venv --python 3.12 .venv
fi
.venv/bin/python -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,14), "Python 3.12 or 3.13 required"'
uv pip install --python .venv/bin/python -e .
echo "Installed in this fork's .venv. Run ./start-demo.command for an offline demo."
