"""Store EXA_API_KEY privately for the local evaluation service; never print it."""

import argparse
import getpass
import json
import os
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-file", type=Path, help="existing file containing only the API key")
    args = parser.parse_args()
    if args.from_file:
        key = args.from_file.read_text().strip()
    else:
        if not os.isatty(0):
            raise SystemExit("Use an interactive terminal for hidden input, or --from-file.")
        key = getpass.getpass("EXA_API_KEY (hidden): ").strip()
    if not key or any(c in key for c in "\r\n\x00"):
        raise SystemExit("Key must be non-empty and contain no line breaks.")
    directory = Path.home() / ".config/agent-harness-eval"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / "exa.env"
    fd, temporary = tempfile.mkstemp(prefix=".exa-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("EXA_API_KEY=" + json.dumps(key) + "\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Saved EXA_API_KEY to {target} (mode 0600). Key not displayed.")


if __name__ == "__main__":
    main()
