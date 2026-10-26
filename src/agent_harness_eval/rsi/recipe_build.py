"""Build a source candidate using frozen integration glue in a separate copy."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent_harness_eval.integrations.builder import build
from agent_harness_eval.integrations.schema import load
from agent_harness_eval.provenance import digest, write_json


def freeze_recipe(recipe_path, directory):
    recipe_path, directory = Path(recipe_path), Path(directory)
    recipe = load(recipe_path)
    if recipe.build.artifact_origin != "source":
        raise ValueError("RSI requires a source-built recipe; registry packages do not evaluate candidate edits")
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "recipe.json", recipe.model_dump(mode="json"))
    for name in recipe.patches:
        source = recipe_path.parent / name
        if source.is_symlink() or not source.resolve().is_relative_to(recipe_path.parent.resolve()):
            raise ValueError("Integration patch escapes recipe directory")
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    write_json(directory / "inputs.json", {name: digest(directory / name) for name in ("recipe.json", *recipe.patches)})
    return directory / "recipe.json"


def verify_recipe(path):
    path = Path(path)
    for name, expected in json.loads((path.parent / "inputs.json").read_text()).items():
        target = path.parent / name
        if target.is_symlink() or not target.resolve().is_relative_to(path.parent.resolve()) or digest(target) != expected:
            raise ValueError("Frozen integration recipe changed")
    return load(path)


def candidate_build(recipe_path, workspace, output, *, executor=None):
    recipe = verify_recipe(recipe_path)
    workspace, output = Path(workspace), Path(output)
    sha = subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
    recipe = recipe.model_copy(update={"source": recipe.source.model_copy(update={"commit": sha})})
    # A candidate clone has a local origin. Restore the declared upstream identity
    # without fetching or changing its source; build checks the exact local SHA.
    subprocess.run(["git", "-C", str(workspace), "remote", "set-url", "origin", recipe.source.repository], check=True)
    return build(recipe, workspace, output / "release", recipe_directory=Path(recipe_path).parent,
                 **({"executor": executor} if executor else {}))


def forbid_glue(workspace, recipe_path, changed_paths):
    recipe = verify_recipe(recipe_path)
    introduced = set()
    for name in recipe.patches:
        lines = (Path(recipe_path).parent / name).read_text().splitlines()
        for i, line in enumerate(lines):
            if line == "--- /dev/null" and i + 1 < len(lines) and lines[i + 1].startswith("+++ b/"):
                introduced.add(lines[i + 1][6:])
    for name in changed_paths:
        if name.startswith(".harness-eval/") or name in introduced:
            raise ValueError("产品 PR 不得包含评测适配文件：" + name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate_build(args.recipe, args.workspace, args.output)


if __name__ == "__main__":
    main()
