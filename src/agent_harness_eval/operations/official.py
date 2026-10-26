"""Standalone official SWE CLI grading uses the same Manager as batch grading."""
import json
import shutil
import signal
from pathlib import Path
from uuid import uuid4

from ..provenance import digest, write_json
from .commands import Commands


def submit(root, predictions, dataset, run_id, output):
    predictions = Path(predictions)
    if predictions.is_symlink() or not predictions.is_file():
        raise ValueError("Predictions must be a regular local file")
    predictions = predictions.resolve()
    # Let the official parser validate the schema; freeze every row, never best-of.
    for line in predictions.read_text(encoding="utf-8").splitlines():
        if line.strip() and not isinstance(json.loads(line), dict):
            raise ValueError("Expected prediction JSONL objects")
    commands = Commands(root)
    key = uuid4().hex
    inputs = commands.store.directory / "official-inputs" / key
    inputs.mkdir(parents=True)
    frozen = inputs / "predictions.jsonl"
    shutil.copy2(predictions, frozen)
    hashes = {"predictions.jsonl": digest(frozen)}
    data = Path(dataset)
    if data.is_file():
        if data.is_symlink():
            raise ValueError("Dataset must not be a symlink")
        target = inputs / ("dataset" + data.suffix)
        shutil.copy2(data, target)
        hashes[target.name] = digest(target)
        dataset = str(target)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = {"predictions": str(frozen), "dataset": str(dataset), "run_id": run_id,
        "inputs": str(inputs), "hashes": hashes, "output": str(output),
        "scorer": "swebench==4.1.0", "original_predictions": str(predictions)}
    write_json(inputs / "request.json", payload)
    return commands.submit("swe-grade", payload, key="swe-grade:" + key)


def perform(root, job_id):
    import os
    from .worker import is_managed_worker
    from ..benchmarks.swe import regrade

    if not is_managed_worker():
        raise ValueError("Official grading requires Manager ownership")
    payload = Commands(root).query(job_id)["payload"]
    for name, expected in payload["hashes"].items():
        if digest(Path(payload["inputs"]) / name) != expected:
            raise ValueError("Frozen official grading inputs changed")
    def cancelled(*_):
        raise KeyboardInterrupt("Official grading cancelled")
    signal.signal(signal.SIGTERM, cancelled)
    os.chdir(payload["output"])
    regrade(Path(payload["predictions"]), payload["dataset"], payload["run_id"], True)
    return {"status": "completed", "output": payload["output"], "input_hashes": payload["hashes"]}
