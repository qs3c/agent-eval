"""Prepare 80 frozen SWE Verified tasks, excluding the prior 21; no model calls.

Run with the existing SWE adapter Python environment.
"""

import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ["PATH"] = str(ROOT / ".tools") + os.pathsep + os.environ["PATH"]
os.environ["PYTHONPATH"] = str(ROOT / "src")
sys.path.insert(0, str(ROOT / ".cache/harbor-source/adapters/swebench/src"))
from swebench_adapter import adapter

from datasets import Dataset

REV = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
SEED = 20261013
target = ROOT / "datasets/swe-live-80"
target.mkdir(parents=True, exist_ok=True)
logs = ROOT / "logs/swe-live-80-preparation"
logs.mkdir(parents=True, exist_ok=True)
arrow = (
    Path.home()
    / f".cache/huggingface/datasets/princeton-nlp___swe-bench_verified/default/0.0.0/{REV}/swe-bench_verified-test.arrow"
)
records = list(Dataset.from_file(str(arrow)))
by_id = {r["instance_id"]: r for r in records}
previous = json.loads((ROOT / "datasets/swe-live-20/selection.json").read_text())
excluded = set(previous["instance_ids"]) | set(previous["excluded"])
assert len(excluded) == 21
ids = random.Random(SEED).sample(sorted(set(by_id) - excluded), 80)
assert len(ids) == len(set(ids)) == 80 and not set(ids) & excluded
assert (
    subprocess.check_output(
        ["git", "-C", ".cache/harbor-source", "rev-parse", "HEAD"], text=True
    ).strip()
    == "b53b8134e1241686dca7759af188f987ecc48e8b"
)
selection = {
    "dataset": previous["dataset"],
    "revision": REV,
    "split": "test",
    "seed": SEED,
    "sampling": "random.Random(seed).sample(sorted(all_ids excluding prior 21), 80)",
    "excluded": sorted(excluded),
    "instance_ids": ids,
    "source_arrow_sha256": hashlib.sha256(arrow.read_bytes()).hexdigest(),
}
selection_path = target / "selection.json"
if selection_path.exists():
    assert json.loads(selection_path.read_text()) == selection
else:
    selection_path.write_text(json.dumps(selection, indent=2) + "\n")
# Use the exact cached revision instead of the adapter's moving dataset default.
adapter.load_dataset = lambda *args, **kwargs: {"test": [by_id[i] for i in ids]}
converter = None
if (
    any(not (target / i / "task.toml").is_file() for i in ids)
    or "--prepare-all" in sys.argv
):
    converter = adapter.SWEBenchAdapter(target, task_ids=ids, max_timeout_sec=600)
for instance in ids:
    task = target / instance
    if not task.exists():
        converter.generate_task(instance, instance)

(target / "records.json").write_text(
    json.dumps([by_id[i] for i in ids], indent=2) + "\n"
)
if "--prepare-all" not in sys.argv:
    print(
        "Frozen 80 task metadata records; images will be prepared by platform batches",
        flush=True,
    )
    raise SystemExit(0)


def status(done, current=None, error=None):
    p = logs / "status.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "prepared": done,
                "total": 80,
                "current": current,
                "error": error,
                "updated_unix": time.time(),
            },
            indent=2,
        )
    )
    tmp.replace(p)


for index, instance in enumerate(ids):
    task = target / instance
    marker = task / "prepared.json"
    status(index, instance)
    if marker.exists():
        continue
    if shutil.disk_usage(ROOT).free < 25 * 1024**3:
        status(index, instance, "Less than 25 GiB disk available; preparation stopped")
        raise SystemExit("Insufficient disk headroom")
    for attempt in range(3):
        print(f"Preparing {index + 1}/80 {instance}, attempt {attempt + 1}", flush=True)
        # Regenerate only this new, unrun task after a partial preparation failure.
        converter.generate_task(instance, instance, overwrite=True)
        try:
            with (logs / f"{instance}.{attempt + 1}.log").open("w") as log:
                subprocess.run(
                    [
                        str(ROOT / ".venv/bin/python"),
                        "-m",
                        "agent_harness_eval.cli",
                        "swe-instrument",
                        str(task),
                    ],
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                subprocess.run(
                    [
                        str(ROOT / ".venv/bin/python"),
                        "scripts/pin-swe-runtime.py",
                        str(task),
                    ],
                    check=True,
                    timeout=1800,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            policy = task / "task.toml"
            text = policy.read_text()
            assert 'network_mode = "no-network"' in text
            policy.write_text(
                text.replace(
                    'network_mode = "no-network"',
                    'network_mode = "allowlist"\nallowed_hosts = ["api.deepseek.com"]',
                )
            )
            assert (
                json.loads((task / "tests/config.json").read_text()) == by_id[instance]
            )
            marker.write_text(
                json.dumps({"instance_id": instance, "prepared_unix": time.time()})
                + "\n"
            )
            break
        except (subprocess.SubprocessError, AssertionError) as error:
            status(index, instance, str(error))
            if attempt == 2:
                raise
            time.sleep(5)
    status(index + 1)
(target / "records.json").write_text(
    json.dumps([by_id[i] for i in ids], indent=2) + "\n"
)
print("Prepared 80 unique tasks; overlap with prior 21 = 0; no model calls", flush=True)
