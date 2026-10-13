#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
experiment_root="$PWD"
export PATH="$experiment_root/.tools:$PATH"

# Validate the frozen sample and prepared environments before any paid request.
.venv/bin/python - <<'PY'
import hashlib, json, tomllib, subprocess, time
from agent_harness_eval.benchmarks.swe import instrument
from pathlib import Path
root = Path('datasets/swe-live-20')
selection = json.loads((root / 'selection.json').read_text())
config = json.loads(Path('experiments/swe-live-20.json').read_text())
ids = selection['instance_ids']
assert len(ids) == len(set(ids)) == 20
assert 'django__django-13741' not in ids
assert {r['instance_id'] for r in json.loads((root / 'records.json').read_text())} == set(ids)
assert [Path(p).name for p in config['benchmark']['tasks']] == ids
for instance in ids:
    task = root / instance
    policy = tomllib.loads((task / 'task.toml').read_text())['agent']
    if policy['network_mode'] != 'allowlist':
        print(f'Preparing {instance}', flush=True)
        if 'python /tests/capture_patch.py' not in (task / 'tests/test.sh').read_text():
            instrument(task)
        for attempt in range(3):
            try:
                subprocess.run(['.venv/bin/python', 'scripts/pin-swe-runtime.py', str(task)], check=True, timeout=1200)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                time.sleep(5)
        cfg = task / 'task.toml'
        text = cfg.read_text()
        assert 'network_mode = "no-network"' in text
        cfg.write_text(text.replace('network_mode = "no-network"', 'network_mode = "allowlist"\nallowed_hosts = ["api.deepseek.com"]'))
    runtime = json.loads((task / 'runtime-provenance.json').read_text())
    assert '@sha256:' in runtime['image']
    assert runtime['image'] in (task / 'environment/Dockerfile').read_text()
    assert (task / 'tests/test_spec.json').is_file()
    policy = tomllib.loads((task / 'task.toml').read_text())['agent']
    assert policy['network_mode'] == 'allowlist'
    assert policy['allowed_hosts'] == ['api.deepseek.com']
# Preserve the corrected BKCrab binary and the previously validated DSH bundle.
previous = json.loads(Path('artifacts/swe-live-bkcrab-corrected/manifest.json').read_text())
assert hashlib.sha256(Path('.cache/bkcrab-eval').read_bytes()).hexdigest() == previous['agent_artifacts']['bkcrab']['binary']
previous = json.loads(Path('artifacts/swe-live-one/manifest.json').read_text())
assert hashlib.sha256(Path('.cache/dsh-bundle03/runtime.tar.gz').read_bytes()).hexdigest() == previous['agent_artifacts']['dsh-standard']['bundle']
print('Preflight passed: 20 frozen tasks, pinned images, original validated agent artifacts', flush=True)
PY
.venv/bin/agent-harness-eval validate experiments/swe-live-20.json
set -a
source .env
set +a
.venv/bin/agent-harness-eval run experiments/swe-live-20.json --output artifacts/swe-live-20
.venv/bin/agent-harness-eval swe-export artifacts/swe-live-20 artifacts/swe-live-20-predictions
mkdir artifacts/swe-live-20-official
cp datasets/swe-live-20/selection.json datasets/swe-live-20/records.json artifacts/swe-live-20-official/
regrade_failed=0
for agent in bkcrab dsh-standard; do
    mkdir "artifacts/swe-live-20-official/$agent"
    (
        cd "artifacts/swe-live-20-official/$agent"
        "$experiment_root/.venv/bin/agent-harness-eval" swe-regrade \
            "$experiment_root/artifacts/swe-live-20-predictions/$agent.attempt-1.jsonl" \
            --dataset "$experiment_root/datasets/swe-live-20/records.json" \
            --run-id "live-20-$agent" --execute
    ) > "artifacts/swe-live-20-official/$agent/console.log" 2>&1 || regrade_failed=1
done
if (( regrade_failed )); then
    echo 'Official regrade command failed; inspect per-agent console.log' >&2
    exit 1
fi
echo 'RUN AND OFFICIAL REGRADING FINISHED; inspect reports for resolved counts and errors'
