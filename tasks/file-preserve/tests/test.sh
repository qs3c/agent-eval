#!/bin/bash
set -eu
mkdir -p /logs/verifier
python3 - <<'PY'
from pathlib import Path
import json
p=Path('/app/result.txt')
artifact=p.is_file() and p.read_bytes()==b'ready\n'
clean={x.name for x in Path('/app').iterdir()}=={'result.txt'}
Path('/logs/verifier/reward.json').write_text(json.dumps({'reward':float(artifact and clean),'artifact':float(artifact),'no_extra_files':float(clean)}))
PY
