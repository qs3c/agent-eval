"""Build BKCrab's evaluation executable from a fixed candidate checkout in Docker."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_harness_eval.provenance import digest, source_manifest, write_json
from agent_harness_eval.rsi.native_adapter import run_container


def build(image, workspace, output):
    before = source_manifest(workspace)
    # Historical source commits may predate the untracked benchmark entrypoint.
    # Apply platform-owned evaluation glue only in a disposable build copy.
    source = output / "source"
    shutil.copytree(
        workspace, source, symlinks=True, ignore=shutil.ignore_patterns(".git")
    )
    patch_sha = None
    if not (source / "cmd/bkcrab-eval/main.go").is_file():
        patch = Path(__file__).resolve().parents[3] / "integrations/bkcrab-eval.patch"
        frozen_patch = output / "evaluation-entrypoint.patch"
        shutil.copyfile(patch, frozen_patch)
        patch_sha = digest(frozen_patch)
        subprocess.run(["git", "init", "--quiet", str(source)], check=True)
        subprocess.run(["git", "apply", str(frozen_patch)], cwd=source, check=True)
    run_container(
        image,
        [(source, "/workspace", False), (output, "/output", False)],
        [
            "sh",
            "-ec",
            """
if [ -d web ] && [ -d /opt/rsi-web/node_modules ]; then
    (
        cd web
        sha256sum -c /opt/rsi-web/checksums
        cp -a /opt/rsi-web/node_modules .
        export NEXT_TELEMETRY_DISABLED=1 CI=1
        node node_modules/next/dist/bin/next build
    )
    mkdir -p internal/setup/web
    cp -a web/out/. internal/setup/web/
fi
go build -mod=readonly -trimpath -o /output/bkcrab-eval ./cmd/bkcrab-eval
""",
        ],
        1800,
        env={
            "HOME": "/tmp/rsi-home",
            "CGO_ENABLED": "0",
            "GOCACHE": "/tmp/go-cache",
            "GOPATH": "/tmp/go",
        },
    )
    after = source_manifest(workspace)
    if after != before:
        raise ValueError("构建期间源码发生变化")
    write_json(
        output / "provenance.json",
        {
            **after,
            "binary_sha256": digest(output / "bkcrab-eval"),
            "profile": "isolated-coding",
            "build_image": image,
            "evaluation_entrypoint_patch_sha256": patch_sha,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.image, args.workspace.resolve(), args.output.resolve())
