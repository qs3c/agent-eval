#!/usr/bin/env python3
"""Build from an already downloaded and locked runtime; never makes model calls."""
import argparse
import json
from agent_harness_eval.native_cli_bundle import build_bundle

parser = argparse.ArgumentParser()
parser.add_argument("agent", choices=["opencode", "hermes"])
parser.add_argument("output")
parser.add_argument("--binary")
parser.add_argument("--source")
parser.add_argument("--python-home")
parser.add_argument("--site-packages")
print(json.dumps(build_bundle(**vars(parser.parse_args())), indent=2))
