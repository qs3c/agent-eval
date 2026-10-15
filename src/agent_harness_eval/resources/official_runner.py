"""Run pinned SWE scoring unchanged; delegate image reclamation to the host."""

import json
import runpy
from pathlib import Path


def configure():
    from swebench.harness import docker_utils
    from swebench.harness.test_spec.test_spec import TestSpec

    # Both per-instance removal and the final global scan belong to the host.
    # In particular, clean=False alone still removes newly created images.
    docker_utils.should_remove = lambda *args, **kwargs: False
    docker_utils.clean_images = lambda *args, **kwargs: None
    original = TestSpec.instance_image_key.fget
    refs = set()

    def image_key(spec):
        key = original(spec)
        if key not in refs:
            refs.add(key)
            Path("official-images.json").write_text(json.dumps(sorted(refs)))
        return key

    TestSpec.instance_image_key = property(image_key)


if __name__ == "__main__":
    configure()
    runpy.run_module("swebench.harness.run_evaluation", run_name="__main__")
