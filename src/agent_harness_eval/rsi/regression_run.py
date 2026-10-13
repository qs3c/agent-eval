"""Execute a frozen Go regression selection without calling a model."""

import argparse
import json
import shlex
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_harness_eval.guardian.evidence import write
from agent_harness_eval.provenance import digest
from agent_harness_eval.rsi.native_adapter import run_container


def main():
    parser = argparse.ArgumentParser()
    for name in ("image", "workspace", "plan", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    workspace, output = Path(args.workspace), Path(args.output)
    output.mkdir(parents=True)
    plan = json.loads(Path(args.plan).read_text())
    results = []
    for index, test in enumerate(plan["tests"]):
        argv = [
            "go",
            "test",
            test["package"],
            "-run",
            "^" + test["name"] + "$",
            "-count=1",
            "-json",
            "-timeout=120s",
        ]
        script = (
            shlex.join(argv) + f" > /output/{index}.jsonl 2> /output/{index}.stderr; "
            f"code=$?; printf '%s' \"$code\" > /output/{index}.exit; exit 0"
        )
        result = {"argv": argv}
        try:
            run_container(
                args.image,
                [(workspace, "/workspace", True), (output, "/output", False)],
                ["sh", "-c", script],
                180,
                env={
                    "HOME": "/tmp/rsi-home",
                    "CGO_ENABLED": "0",
                    "GOCACHE": "/tmp/go-cache",
                    "GOPATH": "/tmp/go",
                    "GOPROXY": "off",
                    "GOTOOLCHAIN": "local",
                },
            )
            result["returncode"] = int((output / f"{index}.exit").read_text())
        except Exception as error:  # noqa: BLE001 - retain execution failures as evidence
            result.update(error=str(error), returncode=None)
        for key, suffix in (("stdout", "jsonl"), ("stderr", "stderr")):
            path = output / f"{index}.{suffix}"
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as stream:
                    data = stream.read(1024 * 1024 + 1)
                result[key] = data[: 1024 * 1024].decode("utf-8", errors="replace")
                result[key + "_sha256"] = digest(path)
                if len(data) > 1024 * 1024:
                    result["truncated"] = True
            else:
                result[key] = ""
        results.append(result)
        write(output / "results.json", results)


if __name__ == "__main__":
    main()
