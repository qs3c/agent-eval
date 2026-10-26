#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
selection="${1:-all}"
case "$selection" in
  all|dsh|bkcrab) ;;
  *) echo "Usage: bash scripts/prepare-native.sh [all|dsh|bkcrab]" >&2; exit 2 ;;
esac
if [[ "$selection" != "dsh" ]]; then
  : "${BKCRAB_SOURCE:?Set BKCRAB_SOURCE to the checkout containing cmd/bkcrab-eval}"
fi
uv sync --locked --python 3.12
mkdir -p .cache
if [[ "$selection" != "bkcrab" ]]; then
  mkdir -p .cache/dsh-runtime
  cp locks/dsh-package.json .cache/dsh-runtime/package.json
  cp locks/dsh-package-lock.json .cache/dsh-runtime/package-lock.json
  npm ci --prefix .cache/dsh-runtime
  .venv/bin/agent-harness-eval bundle-dsh .cache/dsh-runtime .cache/dsh-bundle03
fi
if [[ "$selection" != "dsh" ]]; then
  (cd "$BKCRAB_SOURCE" && CGO_ENABLED=0 go build -trimpath -o "$OLDPWD/.cache/bkcrab-eval" ./cmd/bkcrab-eval)
  .venv/bin/agent-harness-eval snapshot-bkcrab "$BKCRAB_SOURCE" .cache/bkcrab-eval .cache/bkcrab-provenance.json
fi
