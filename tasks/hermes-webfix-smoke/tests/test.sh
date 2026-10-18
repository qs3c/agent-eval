#!/bin/bash
set -eu
mkdir -p /logs/verifier
python3 - <<'PY'
from pathlib import Path
import json
p=Path('/app/result.txt')
ok=p.is_file() and p.read_bytes()==b'ready\n'
Path('/logs/verifier/reward.json').write_text(json.dumps({'reward':float(ok)}))
PY
