#!/bin/sh
set -eu
cd /workspace/web
# Never substitute dependencies for a different candidate lockfile.
sha256sum -c /opt/rsi-web/checksums
cp -a /opt/rsi-web/node_modules .
export NEXT_TELEMETRY_DISABLED=1
export CI=1
node node_modules/next/dist/bin/next build
cd /workspace
mkdir -p internal/setup/web
cp -a web/out/. internal/setup/web/
exec go test ./...
