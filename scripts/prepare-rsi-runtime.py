"""Package the already prepared native Agents for the browser executor catalog."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path.cwd())
parser.add_argument("--artifacts-root", type=Path)
args = parser.parse_args()
root = args.root.resolve()
artifacts = (args.artifacts_root or root).resolve() / ".cache"
stage = root / ".cache/rsi-native-build"
stage.mkdir(parents=True, exist_ok=True)
shutil.copyfile(root / "integrations/rsi/Dockerfile", stage / "Dockerfile")
shutil.copyfile(artifacts / "bkcrab-eval", stage / "bkcrab-eval")
shutil.copyfile(artifacts / "dsh-bundle03/runtime.tar.gz", stage / "runtime.tar.gz")
subprocess.run(
    ["docker", "build", "-t", "agent-harness-eval/rsi-native:1", str(stage)], check=True
)
image = subprocess.check_output(
    [
        "docker",
        "image",
        "inspect",
        "agent-harness-eval/rsi-native:1",
        "--format",
        "{{.Id}}",
    ],
    text=True,
).strip()
bkcrab = json.loads((artifacts / "bkcrab-provenance.json").read_text())
dsh = json.loads((artifacts / "dsh-bundle03/provenance.json").read_text())
record = {
    "image": image,
    "executors": {"bkcrab": bkcrab["binary_sha256"][:16], "dsh": dsh["version"]},
}
(root / ".cache/rsi-native.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record))
