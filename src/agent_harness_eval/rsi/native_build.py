"""Build BKCrab's evaluation executable from a fixed candidate checkout in Docker."""

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_harness_eval.provenance import digest, source_manifest, write_json
from agent_harness_eval.rsi.native_adapter import run_container


def build(image, workspace, output):
    before = source_manifest(workspace)
    run_container(
        image,
        [(workspace, "/workspace", False), (output, "/output", False)],
        [
            "sh",
            "-ec",
            """
if [ -f /installed-agent/check-bkcrab.sh ]; then
    sh /installed-agent/check-bkcrab.sh
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
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.image, args.workspace.resolve(), args.output.resolve())
