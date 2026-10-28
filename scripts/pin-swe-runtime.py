"""Pin the generated task image and inject the locally installed uv executable."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from agent_harness_eval.provenance import digest, write_json

task = Path(sys.argv[1])
uv = Path(shutil.which("uv") or ".tools/uv").resolve()
if subprocess.check_output([str(uv), "--version"], text=True).split()[1] != "0.13.0":
    raise SystemExit("Use uv 0.13.0 for this locked smoke runtime")
dockerfile = task / "environment/Dockerfile"
image = re.search(r"^FROM (\S+)", dockerfile.read_text(), re.MULTILINE).group(1)
probe = subprocess.run(
    ["docker", "image", "inspect", image],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
)
if probe.returncode:
    subprocess.run(["docker", "pull", image], check=True)
identity = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
pinned = image if "@sha256:" in image else identity["RepoDigests"][0]
if "@sha256:" in image and image not in identity["RepoDigests"]:
    raise SystemExit("Pinned image digest does not match local image")
ready = all(
    (task / p).is_file()
    for p in [
        "grader-provenance.json",
        "grader-files.json",
        "environment/grader-runtime.tar",
        "tests/test_spec.json",
    ]
)
if ready:
    previous = json.loads((task / "grader-provenance.json").read_text())
    if digest(task / "environment/grader-runtime.tar") != previous["archive_sha256"]:
        raise SystemExit("Prepared grader archive hash mismatch")
    print("Pinned runtime already prepared")
    raise SystemExit(0)
previous = (
    json.loads((task / "runtime-provenance.json").read_text())
    if (task / "runtime-provenance.json").exists()
    else {}
)
shutil.copyfile(uv, task / "environment/uv")
write_json(
    task / "runtime-provenance.json",
    {
        "upstream_dockerfile_sha256": previous.get(
            "upstream_dockerfile_sha256", digest(dockerfile)
        ),
        "image": pinned,
        "uv_version": "0.13.0",
        "uv_sha256": digest(uv),
    },
)
dockerfile.write_text(
    f"FROM {pinned}\nWORKDIR /testbed\nCOPY uv /root/.local/bin/uv\nRUN chmod 755 /root/.local/bin/uv && mkdir -p /logs\n"
)
# Pinning images/grading dependencies must not change the task's agent network
# contract. Batch preparation handles required API hosts for restricted tasks.
records = [
    json.loads(p.read_text()) for p in sorted(task.parent.glob("*/tests/config.json"))
]
write_json(task.parent / "records.json", records)

# Pre-install the public grading runtime outside the trial. This avoids network
# dependency installation contaminating verifier timeouts or scores.
archive = task / "environment/grader-runtime.tar"
if archive.exists():
    archive.unlink()  # Only a private preparation input, before any trial runs.
resolved = json.loads(
    subprocess.check_output(
        [
            str(uv),
            "run",
            "--no-project",
            "--python",
            "3.11",
            "--with",
            "swebench==4.0.3",
            "--with",
            "datasets==2.16.1",
            "--with",
            "pyarrow==20.0.0",
            "--with",
            "fastcore<1.11",
            "python",
            str(Path(__file__).with_name("materialize-grader.py").resolve()),
            str((task / "environment/grader-runtime.tar").resolve()),
            str((task / "tests/config.json").resolve()),
        ],
        text=True,
    )
)
write_json(
    task / "grader-provenance.json",
    {
        "packages": resolved["packages"],
        "python": Path(resolved["python"]).name,
        "archive_sha256": resolved["bundle"]["sha256"],
        "file_count": resolved["bundle"]["file_count"],
        "inventory_sha256": resolved["bundle"]["inventory_sha256"],
    },
)
shutil.copyfile(
    Path(resolved["bundle"]["path"]).parent / resolved["bundle"]["inventory"],
    task / "grader-files.json",
)
with dockerfile.open("a") as f:
    f.write("ADD grader-runtime.tar /opt/agent-harness-eval-grader/\n")
verifier = task / "tests/test.sh"
verifier.write_text(
    verifier.read_text().replace(
        "uv run parser.py",
        "PYTHONHOME=/opt/agent-harness-eval-grader/python PYTHONPATH=/opt/agent-harness-eval-grader/site /opt/agent-harness-eval-grader/python/bin/python3.11 parser.py",
    )
)

write_json(task / "tests/test_spec.json", resolved["test_spec"])
verifier.write_text(
    verifier.read_text()
    .replace("import make_test_spec", "import TestSpec")
    .replace(
        "make_test_spec(datum)", 'TestSpec(**json.load(open("/tests/test_spec.json")))'
    )
)
