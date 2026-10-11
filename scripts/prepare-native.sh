#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${BKCRAB_SOURCE:?Set BKCRAB_SOURCE to the checkout containing cmd/bkcrab-eval}"
uv sync --locked --python 3.12
mkdir -p .cache/dsh-runtime
cp locks/dsh-package.json .cache/dsh-runtime/package.json
cp locks/dsh-package-lock.json .cache/dsh-runtime/package-lock.json
npm ci --prefix .cache/dsh-runtime
(cd "$BKCRAB_SOURCE" && CGO_ENABLED=0 go build -trimpath -o "$OLDPWD/.cache/bkcrab-eval" ./cmd/bkcrab-eval)
.venv/bin/agent-harness-eval snapshot-bkcrab "$BKCRAB_SOURCE" .cache/bkcrab-eval .cache/bkcrab-provenance.json
.venv/bin/agent-harness-eval bundle-dsh .cache/dsh-runtime .cache/dsh-bundle03
