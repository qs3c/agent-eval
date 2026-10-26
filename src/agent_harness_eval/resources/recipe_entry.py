"""Container-only Python callable protocol. No platform modules are required."""

import importlib
import json
import sys
from pathlib import Path


def main():
    module, function = sys.argv[1].split(":", 1)
    request = json.loads(Path(sys.argv[2]).read_text())
    result = getattr(importlib.import_module(module), function)(request)
    if not isinstance(result, dict) or result.get("schema_version") != 1 or result.get("status") not in {"completed", "failed"}:
        raise ValueError("Callable must return schema_version=1 and completed/failed status")
    (Path(request["output_dir"]) / "agent-result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main()
