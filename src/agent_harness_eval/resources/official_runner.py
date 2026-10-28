"""Run pinned SWE scoring with atomic patch application and host-owned cleanup."""

import ast
import hashlib
import importlib.util
import json
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
    mapping_path = Path("official-image-map.json")
    mapping = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    if not isinstance(mapping, dict) or any(
        not isinstance(ref, str) or "@sha256:" not in ref for ref in mapping.values()
    ):
        raise ValueError("Official grading image mapping must contain digest-pinned refs")

    def image_key(spec):
        if mapping and spec.instance_id not in mapping:
            raise ValueError("Missing frozen image for " + spec.instance_id)
        key = mapping[spec.instance_id] if mapping else original(spec)
        if key not in refs:
            refs.add(key)
            Path("official-images.json").write_text(json.dumps(sorted(refs)))
        return key

    TestSpec.instance_image_key = property(image_key)


def atomic_harness(source: str, filename: str):
    """Change only delivery; leave upstream test execution and scoring unchanged."""
    tree = ast.parse(source, filename)
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "GIT_APPLY_CMDS" for t in node.targets)]
    if len(assignments) != 1 or ast.literal_eval(assignments[0].value) != [
        "git apply --verbose", "git apply --verbose --reject", "patch --batch --fuzz=5 -p1 -i"
    ]:
        raise RuntimeError("Unsupported SWE harness patch application policy")
    assignments[0].value = ast.List(elts=[ast.Constant("git apply --verbose")], ctx=ast.Load())
    ast.fix_missing_locations(tree)
    return compile(tree, filename, "exec")


if __name__ == "__main__":
    configure()
    spec = importlib.util.find_spec("swebench.harness.run_evaluation")
    source = Path(spec.origin).read_text()
    code = atomic_harness(source, spec.origin)
    Path("official-execution-policy.json").write_text(json.dumps({
        "upstream_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "patch_application": ["git apply --verbose"],
        "fallbacks_disabled": ["git apply --reject", "patch"],
        "scoring": "unchanged",
    }))
    exec(code, {"__name__": "__main__", "__file__": spec.origin,
                "__package__": "swebench.harness"})
