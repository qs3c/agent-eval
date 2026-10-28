"""Package the official Standard preset with audited transport/compatibility fixes."""

import io
import json
import shutil
import tarfile
from pathlib import Path

import yaml

from .provenance import digest, write_json
from .dsh_compat import SEARCH_MODULE, patched_search_module, patched_fetch_modules


class JS(str):
    pass


class Loader(yaml.SafeLoader):
    pass


class Dumper(yaml.SafeDumper):
    pass


Loader.add_constructor(
    "tag:yaml.org,2002:js", lambda loader, n: JS(loader.construct_scalar(n))
)
Dumper.add_representer(
    JS, lambda d, x: d.represent_scalar("tag:yaml.org,2002:js", str(x))
)


def audit_standard(path: Path) -> dict:
    data = yaml.load(path.read_text(), Loader=Loader)
    row = next(
        r for op in data for r in op.get("insert", []) if r["id"] == "preset-standard"
    )
    if row["config"]["id"] != "standard":
        raise ValueError("not Standard")
    names = set()

    def visit(rows):
        for r in rows:
            if r.get("disabled") is True:
                continue
            names.add(r["name"])
            if r.get("group"):
                visit(r["config"])

    visit(row["config"]["plugins"])
    required = {
        "persona",
        "tool-bash",
        "tool-fs",
        "agent-instructions",
        "compaction-basic",
        "compaction-tool-result-pruner",
        "tool-subagent",
        "tool-todo",
    }
    missing = {"@deepseek-ai/dsh-" + n for n in required} - names
    if missing:
        raise ValueError(f"Standard missing required modules: {sorted(missing)}")
    return {
        "preset": "standard",
        "preset_sha256": digest(path),
        "modules": sorted(names),
        "config_audit": "passed",
        "runtime_audit": "required on every launch",
    }


def build_bundle(runtime: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    modules = runtime / "node_modules"
    web = modules / "@deepseek-ai/dsh-web-app"
    cli = modules / "@deepseek-ai/dsh/package.json"
    version = json.loads(cli.read_text())["version"]
    search_module, search_fix = patched_search_module((runtime / SEARCH_MODULE).read_bytes())
    patched_files, fetch_fixes = patched_fetch_modules(runtime)
    patched_files[SEARCH_MODULE] = search_module
    original = web / "presets/standard.patch.yml"
    audit = audit_standard(original)
    shutil.copyfile(original, output / "standard.patch.yml")
    web_patch = yaml.load((web / "cordis.patch.yml").read_text(), Loader=Loader)
    # Exactly the Web agent-plane disable set, without mounting browser/server UI.
    patch = [r for r in web_patch if r.get("disabled") is True and "id" in r]
    patch += [
        {"id": "sdk-jsonrpc-server", "disabled": True},
        {"id": "workspace-dependencies", "disabled": True},
        {"id": "skill-office", "disabled": True},
    ]
    support_ids = {
        "schedule",
        "subagent-model-selection-settings",
        "agent-preset-registry",
        "session-controller",
        "file-upload",
        "connection",
        "workspace",
    }
    support = [
        r for op in web_patch for r in op.get("insert", []) if r["id"] in support_ids
    ]
    if {r["id"] for r in support} != support_ids:
        raise ValueError("upstream host dependencies changed; review composition")
    for row in support:
        if row["id"] == "session-controller":
            row["config"] = {"nativeOpen": False}
        if row["id"] == "connection":
            row.pop("inject", None)
            row["config"] = {"trustedHosts": []}
    patch.append({"insert": support})
    patch.append(
        {
            "insert": [
                {"id": "eval-driver", "name": "/installed-agent/dsh/eval-driver.mjs"}
            ]
        }
    )
    (output / "transport.patch.yml").write_text(
        yaml.dump(patch, Dumper=Dumper, sort_keys=False)
    )
    resource = Path(__file__).parent / "resources"
    shutil.copyfile(resource / "dsh-driver.mjs", output / "eval-driver.mjs")
    shutil.copyfile(resource / "mock_model.py", output / "mock_model.py")
    audit.update(
        version=version,
        transport_sha256=digest(output / "transport.patch.yml"),
        driver_sha256=digest(output / "eval-driver.mjs"),
        compatibility_patches=[search_fix, *fetch_fixes],
        web_fetch_review=True,
        differences=[
            "SDK startup transport plus one-task driver; official Standard preset mounted through agentPresets.mount",
            "no browser UI",
            "container provides isolation; permission mode danger-full-access",
            "office runtime extras disabled",
            "fresh state; no external user skills",
            "DeepSeek search adapter preserves response text as normalized content (audited compatibility patch)",
            "Optional web_fetch model-visible content review through the evaluation relay",
        ],
    )
    write_json(output / "audit.json", audit)
    node = Path(shutil.which("node")).resolve()
    with tarfile.open(output / "runtime.tar.gz", "w:gz", compresslevel=1) as archive:
        archive.add(modules, arcname="node_modules", filter=lambda info: None if info.name in patched_files else info)
        # Overlay into the new archive only: never mutate npm cache or old bundles.
        for name, content in patched_files.items():
            info = tarfile.TarInfo(name)
            info.mode = 0o644
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        archive.add(node, arcname="node")
        for name in (
            "standard.patch.yml",
            "transport.patch.yml",
            "eval-driver.mjs",
            "mock_model.py",
            "audit.json",
        ):
            archive.add(output / name, arcname=name)
    write_json(
        output / "provenance.json",
        {
            **audit,
            "bundle_sha256": digest(output / "runtime.tar.gz"),
            "node_sha256": digest(node),
            "npm_lock_sha256": digest(runtime / "package-lock.json"),
        },
    )
