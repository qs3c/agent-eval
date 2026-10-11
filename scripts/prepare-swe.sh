#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
instance="${1:-django__django-13741}"
if [[ ! -d .cache/harbor-source ]]; then
  git clone --branch v0.24.0 --depth 1 https://github.com/harbor-framework/harbor.git .cache/harbor-source
fi
expected=b53b8134e1241686dca7759af188f987ecc48e8b
actual="$(git -C .cache/harbor-source rev-parse HEAD)"
[[ "$actual" == "$expected" ]] || { echo 'Harbor source revision mismatch' >&2; exit 1; }
uv run --project .cache/harbor-source/adapters/swebench --python 3.13 --locked swebench --instance-id "$instance" --output-dir "$PWD/datasets/swe-smoke" --timeout 600
.venv/bin/agent-eval swe-instrument "datasets/swe-smoke/$instance"
.venv/bin/python scripts/pin-swe-runtime.py "datasets/swe-smoke/$instance"
