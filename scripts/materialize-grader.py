"""Called inside uv's temporary grading environment before it is removed."""

import importlib.metadata
import json
import sys
from dataclasses import asdict
from pathlib import Path

from swebench.harness.test_spec.test_spec import make_test_spec

target = Path(sys.argv[1])
distributions = list(importlib.metadata.distributions())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_harness_eval.runtime_bundle import bundle_runtime, link_archive

sources = [("python", Path(sys.base_prefix))]
sources += [
    ("site", Path(p)) for p in sorted({str(d.locate_file("")) for d in distributions})
]
bundle = bundle_runtime(
    sources, Path(__file__).resolve().parents[1] / ".cache/grader-bundles"
)
link_archive(bundle["path"], target)
print(
    json.dumps(
        {
            "python": sys.base_prefix,
            "bundle": bundle,
            "test_spec": asdict(
                make_test_spec(json.loads(Path(sys.argv[2]).read_text()))
            ),
            "packages": {d.metadata["Name"]: d.version for d in distributions},
        }
    )
)
