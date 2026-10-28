"""Public native executors use immutable, locally built Recipe releases."""

import json
import re
from pathlib import Path

from ..integrations.catalog import recipe_path
from ..integrations.schema import load
from ..integrations.store import Releases, verify

EXECUTORS = {"opencode": "OpenCode", "dsh": "DSH", "hermes": "Hermes"}


def runtime(root):
    path = Path(root) / ".cache/platform-runtime.json"
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if re.fullmatch(r"sha256:[0-9a-f]{64}", value.get("image", "")) else {}


def resolve(root, executor, release_id=None):
    if executor not in EXECUTORS:
        raise ValueError("请选择 OpenCode、DSH 或 Hermes")
    directory = Releases(root).directory
    if release_id and not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise ValueError("Invalid executor release id")
    paths = [directory / release_id] if release_id else sorted(directory.iterdir(), reverse=True)
    expected = load(recipe_path(executor))
    for path in paths:
        if not path.is_dir() or (path / "disabled.json").exists():
            continue
        try:
            receipt = verify(path)
        except (OSError, ValueError, KeyError):
            if release_id:
                raise ValueError("冻结的执行器 Release 无效")
            continue
        if receipt["release_id"] != path.name:
            continue
        # New requests use the shipped recipe. Frozen jobs retain their exact recipe.
        if receipt["recipe"]["id"] == executor and (release_id or receipt["release"]["recipe_digest"] == expected.digest):
            return path, receipt
    raise ValueError(f"请先在内置 Agent 中构建 {EXECUTORS[executor]}；所选 Release 不存在或已禁用")


def catalog(root):
    native = runtime(root)
    result = []
    for executor, name in EXECUTORS.items():
        entry = {"id": executor, "name": name, "installed": False, "ready": False,
                 "release_id": None, "protocol": load(recipe_path(executor)).model_protocol}
        try:
            _, receipt = resolve(root, executor)
            entry.update(installed=True, release_id=receipt["release_id"], ready=bool(native))
            entry["reason"] = "已就绪" if native else "请运行 python scripts/prepare-rsi-runtime.py 准备通用平台容器"
        except ValueError as error:
            entry["reason"] = str(error)
        result.append(entry)
    return result
