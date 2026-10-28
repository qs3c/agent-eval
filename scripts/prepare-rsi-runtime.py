"""Build a generic platform container; native agents come from local Releases."""
import argparse
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path.cwd())
args = parser.parse_args()
root = args.root.resolve()
# This container has no user agent or user source baked into it.
subprocess.run(["docker", "build", "-t", "agent-harness-eval/platform:1",
                "-f", str(root / "integrations/rsi/Dockerfile"), str(root / "integrations/rsi")], check=True)
image = subprocess.check_output(["docker", "image", "inspect", "agent-harness-eval/platform:1",
                                 "--format", "{{.Id}}"], text=True).strip()
record = {"schema_version": 1, "image": image}
(root / ".cache").mkdir(exist_ok=True)
(root / ".cache/platform-runtime.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record))
