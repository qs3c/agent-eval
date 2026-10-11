"""Called inside uv's temporary grading environment before it is removed."""

import importlib.metadata
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from swebench.harness.test_spec.test_spec import make_test_spec

target = Path(sys.argv[1])
distributions = list(importlib.metadata.distributions())
shutil.copytree(
    sys.base_prefix,
    target / "python",
    dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("__pycache__"),
)
# uv run uses an overlay; sysconfig.purelib can be an empty forwarding directory.
for source in {str(d.locate_file("")) for d in distributions}:
    shutil.copytree(
        source,
        target / "site",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "_uv_ephemeral_overlay.pth"),
    )
(target / "site/_uv_ephemeral_overlay.pth").unlink(missing_ok=True)
print(
    json.dumps(
        {
            "python": sys.base_prefix,
            "test_spec": asdict(
                make_test_spec(json.loads(Path(sys.argv[2]).read_text()))
            ),
            "packages": {d.metadata["Name"]: d.version for d in distributions},
        }
    )
)
