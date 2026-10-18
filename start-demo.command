#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/harness2-demo ]]; then
  ./scripts/local-install.sh
fi
exec .venv/bin/harness2-demo --output runs/local-demo --k 2 "$@"
