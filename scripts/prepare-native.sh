#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
selection="${1:-dsh}"
case "$selection" in
  all|dsh) ;;
  *) echo "Usage: bash scripts/prepare-native.sh [dsh]" >&2; exit 2 ;;
esac
uv sync --locked --python 3.12
mkdir -p .cache/dsh-runtime
cp locks/dsh-package.json .cache/dsh-runtime/package.json
cp locks/dsh-package-lock.json .cache/dsh-runtime/package-lock.json
npm ci --prefix .cache/dsh-runtime
.venv/bin/agent-harness-eval bundle-dsh .cache/dsh-runtime .cache/dsh-bundle03
