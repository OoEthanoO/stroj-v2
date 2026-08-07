#!/usr/bin/env bash
# Start the judge. Everything lives in ./data — delete it for a clean slate.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
exec .venv/bin/python -m stroj serve "$@"
