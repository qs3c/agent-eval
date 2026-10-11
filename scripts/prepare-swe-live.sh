#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# First prepare the frozen offline task with scripts/prepare-swe.sh.
python3 - <<'PY'
from pathlib import Path
import shutil
source = Path('datasets/swe-smoke/django__django-13741')
target = Path('datasets/swe-live-one') / source.name
if target.exists():
    raise SystemExit('Live task already exists; refusing to overwrite')
shutil.copytree(source, target)
config = target / 'task.toml'
text = config.read_text()
assert 'network_mode = "no-network"' in text
config.write_text(text.replace('network_mode = "no-network"', 'network_mode = "allowlist"\nallowed_hosts = ["api.deepseek.com"]'))
PY
.venv/bin/agent-eval validate experiments/swe-live-one.json
