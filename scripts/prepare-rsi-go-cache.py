"""Seed an existing RSI image with Go modules for offline candidate checks.

Run after prepare-rsi-runtime.py. Registration happens only after a successful
image build. Existing candidates keep their original frozen image reference.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path.cwd())
parser.add_argument("--module-cache", type=Path, required=True)
parser.add_argument(
    "--web-directory", type=Path, required=True,
    help="BKCrab web directory with installed, lockfile-matched dependencies",
)
args = parser.parse_args()
root = args.root.resolve()
source = args.module_cache.resolve()
if not (source / "cache/download").is_dir():
    parser.error("--module-cache must be a populated Go module cache")
web = args.web_directory.resolve()
installed_lock = web / "node_modules/.pnpm/lock.yaml"
if not (web / "node_modules/next/dist/bin/next").is_file():
    parser.error("Install the web lockfile dependencies before preparing the image")
if (
    not installed_lock.is_file()
    or installed_lock.read_bytes() != (web / "pnpm-lock.yaml").read_bytes()
):
    parser.error("Installed pnpm dependencies do not match the source lockfile")
registry = root / ".cache/rsi-native.json"
record = json.loads(registry.read_text())
base = record.get("go_cache", {}).get("base_image", record["image"])
if not re.fullmatch(r"sha256:[0-9a-f]{64}", base):
    parser.error("Existing runtime must use a fixed Docker image SHA")
stage = Path(tempfile.mkdtemp(prefix="rsi-go-build-", dir=root / ".cache"))
# A fresh snapshot avoids overwriting Go's read-only module files on retries.
shutil.copytree(source, stage / "modules")
shutil.copytree(web / "node_modules", stage / "web-node-modules", symlinks=True)
(stage / "web-checksums").write_text(
    "".join(
        hashlib.sha256((web / name).read_bytes()).hexdigest() + "  " + name + "\n"
        for name in ("package.json", "pnpm-lock.yaml")
    )
)
shutil.copyfile(root / "integrations/rsi/check-bkcrab.sh", stage / "check-bkcrab.sh")
base_tag = "agent-harness-eval/rsi-base:" + base.removeprefix("sha256:")
subprocess.run(["docker", "tag", base, base_tag], check=True)
(stage / "Dockerfile").write_text(
    f"FROM {base_tag}\n"
    "COPY modules /tmp/go/pkg/mod\n"
    "COPY web-node-modules /opt/rsi-web/node_modules\n"
    "COPY web-checksums /opt/rsi-web/checksums\n"
    "COPY check-bkcrab.sh /installed-agent/check-bkcrab.sh\n"
    "ENV GOPROXY=off GOTOOLCHAIN=local\n"
)
subprocess.run(
    ["docker", "build", "-t", "agent-harness-eval/rsi-native:go-offline", str(stage)],
    check=True,
)
image = subprocess.check_output(
    [
        "docker", "image", "inspect", "agent-harness-eval/rsi-native:go-offline",
        "--format", "{{.Id}}",
    ],
    text=True,
).strip()
record.update(image=image, go_cache={"base_image": base, "mode": "offline"})
temporary = registry.with_suffix(".json.tmp")
temporary.write_text(json.dumps(record, indent=2) + "\n")
temporary.replace(registry)
print(json.dumps(record))
