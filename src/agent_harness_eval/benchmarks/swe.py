"""SWE-specific export boundary; no SWE assumptions in the agents or runner."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..manifests import load_manifest
from ..provenance import digest, write_json
from ..report import task_key


def instrument(task: Path):
    datum = json.loads((task / "tests/config.json").read_text())
    if not all(datum.get(k) for k in ("base_commit", "instance_id")):
        raise ValueError("SWE task metadata missing")
    script = task / "tests/test.sh"
    text = script.read_text()
    marker = "python /tests/capture_patch.py"
    if marker in text:
        raise ValueError("task already instrumented")
    # Must precede the upstream test cleanup/reset and hidden test patch.
    text = text.replace("set -euo pipefail", "set -euo pipefail\n" + marker, 1)
    if marker not in text:
        raise ValueError("unrecognized upstream verifier template")
    # datasets 2.16.1 imports PyExtensionType, removed by PyArrow 21+.
    # Preserve upstream grading code while constraining that incompatible dependency.
    text = text.replace('"fastcore<1.11"]', '"fastcore<1.11", "pyarrow==20.0.0"]')
    text = text.replace(
        "# Reward logging for Harbor.",
        "if [ ! -s /logs/verifier/report.json ]; then exit 2; fi\n\n# Reward logging for Harbor.",
    )
    source = Path(__file__).parents[1] / "resources/capture_patch.py"
    shutil.copyfile(source, task / "tests/capture_patch.py")
    write_json(
        task / "swe-provenance.json",
        {
            "instance_id": datum["instance_id"],
            "base_commit": datum["base_commit"],
            "upstream_verifier_sha256": digest(script),
            "capture_sha256": digest(source),
            "generator_harbor_commit": "b53b8134e1241686dca7759af188f987ecc48e8b",
            "dataset_record_sha256": digest(task / "tests/config.json"),
            "harbor_parser_version": "4.0.3",
            "official_regrade_version": "4.1.0",
        },
    )
    script.write_text(text)


def export(run: Path, output: Path):
    manifest = load_manifest(run)
    exp = manifest["experiment"]
    output.mkdir(parents=True, exist_ok=False)
    provenance = []
    for agent in exp["agents"]:
        attempts = [[] for _ in range(exp["attempts"])]
        for task in manifest["tasks"]:
            datum = json.loads((run / "tasks" / task / "tests/config.json").read_text())
            trials = []
            for p in (run / "jobs" / agent["id"]).glob("*/result.json"):
                result = json.loads(p.read_text())
                if task_key(result) == task:
                    trials.append((result.get("started_at", ""), p, result))
            trials.sort(key=lambda row: (row[0], str(row[1])))
            if len(trials) > exp["attempts"]:
                raise ValueError("more trials than configured attempts")
            for index in range(exp["attempts"]):
                trial = trials[index] if index < len(trials) else None
                patch_path = trial[1].parent / "verifier/model.patch" if trial else None
                patch = (
                    patch_path.read_text() if patch_path and patch_path.exists() else ""
                )
                attempts[index].append(
                    {
                        "instance_id": datum["instance_id"],
                        "model_patch": patch,
                        "model_name_or_path": agent["id"],
                    }
                )
                provenance.append(
                    {
                        "agent": agent["id"],
                        "attempt": index + 1,
                        "instance_id": datum["instance_id"],
                        "trial": str(trial[1].relative_to(run)) if trial else None,
                        "patch_captured": bool(patch_path and patch_path.exists()),
                        "exception": trial[2].get("exception_info")
                        if trial
                        else "missing trial",
                    }
                )
        for index, rows in enumerate(attempts, 1):
            if len({r["instance_id"] for r in rows}) != len(rows):
                raise ValueError("duplicate instance IDs")
            (output / f"{agent['id']}.attempt-{index}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows)
            )
    write_json(
        output / "export-manifest.json",
        {
            "source_manifest_sha256": digest(run / "manifest.json"),
            "policy": "one file per agent/attempt, chronological per task; missing patches are empty and counted",
            "trials": provenance,
        },
    )


def _uv_executable():
    # systemd/console workers need not inherit the shell's .tools PATH entry.
    installed = shutil.which("uv")
    if installed:
        return str(Path(installed).resolve())
    bundled = Path(__file__).resolve().parents[3] / ".tools" / "uv"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    raise FileNotFoundError(
        "Official grading requires uv: install it on the service PATH "
        f"or provide an executable at {bundled}"
    )


def regrade_command(predictions: Path, dataset: str, run_id: str):
    if not predictions.is_file():
        raise ValueError("predictions file missing")
    return [
        _uv_executable(),
        "run",
        "--no-project",
        "--python",
        "3.13",
        "--with",
        "swebench==4.1.0",
        "python",
        str(Path(__file__).resolve().parents[1] / "resources/official_runner.py"),
        "--dataset_name",
        dataset,
        "--predictions_path",
        str(predictions.resolve()),
        "--run_id",
        run_id,
        "--max_workers",
        "1",
    ]


def regrade(predictions: Path, dataset: str, run_id: str, execute: bool):
    command = regrade_command(predictions, dataset, run_id)
    import shlex

    print(shlex.join(command))
    if execute:
        from uuid import uuid4

        from ..regrading import gate, run_command

        work = Path.cwd()
        log = work / ("swe-regrade-" + uuid4().hex + ".log")
        with gate("batches") as fd:
            code = run_command(command, work, log, (fd,))
        print(f"Official grading log: {log}")
        if code:
            raise subprocess.CalledProcessError(code, command)
