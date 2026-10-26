"""Server-owned native executors; browser settings never contain commands or paths."""

import os
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, SecretStr

from ..guardian.evidence import read, write
from ..model_pool import ModelInput, ModelPool
from .models import Profile, Strict

EXECUTORS = {"bkcrab": "BKCrab", "dsh": "DSH"}
TARGETS = {
    "bkcrab": "bkcrab",
    "dsh-standard": "deepseek-harness",
    "dsh": "deepseek-harness",
}


class ExecutorSettings(Strict):
    executor: Literal["bkcrab", "dsh"]
    model: str | None = Field(
        default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$"
    )
    model_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    timeout_seconds: int = Field(default=1800, ge=60, le=7200)
    api_key: SecretStr | None = Field(default=None, max_length=4096)


def roots(root):
    return list(
        dict.fromkeys(
            [Path(root).resolve(), Path(root).resolve().parent / "agent-harness-eval"]
        )
    )


def runtime(root):
    for directory in roots(root):
        value = read(directory / ".cache/rsi-native.json", {})
        if re.fullmatch(r"sha256:[0-9a-f]{64}", value.get("image", "")):
            return value
    return {}


def credential(root):
    """Reuse only the intended model credential, never import arbitrary dotenv settings."""
    path = Path(root) / ".rsi/executor-key"
    if path.is_file() and not path.is_symlink():
        value = path.read_text().strip()
        if value:
            return value
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    for directory in roots(root):
        env = directory / ".env"
        if not env.is_file() or env.is_symlink():
            continue
        for line in env.read_text().splitlines():
            name, _, value = line.removeprefix("export ").partition("=")
            if name.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip("\"'")
    return ""


def settings(root):
    value = read(Path(root) / ".rsi/executor-settings.json", {})
    if not value:
        return None
    return ExecutorSettings.model_validate(value)


def catalog(root):
    saved, native = settings(root), runtime(root)
    pool = ModelPool(root)
    public = pool.public()
    executor = saved.executor if saved else "bkcrab"
    selected = public["bindings"].get("executor_" + executor)
    entry = next((m for m in public["models"] if m["id"] == selected), None)
    has_key = bool(entry and entry["has_api_key"])
    return {
        "options": [
            {
                "id": key,
                "name": name,
                "installed": key in native.get("executors", {}),
                "description": "BKCrab 原生编码 Agent"
                if key == "bkcrab"
                else "DeepSeek Harness · Standard",
            }
            for key, name in EXECUTORS.items()
        ],
        "configured": saved is not None,
        "executor": saved.executor if saved else "bkcrab",
        "model": entry["name"] if entry else None,
        "model_id": selected,
        "models": public["models"],
        "bindings": public["bindings"],
        "timeout_seconds": saved.timeout_seconds if saved else 1800,
        "has_api_key": has_key,
        "ready": bool(
            saved and has_key and saved.executor in native.get("executors", {})
        ),
    }


def save_settings(root, value: ExecutorSettings):
    if value.model_id:
        ModelPool(root).resolve("executor_" + value.executor, value.model_id)
    directory = Path(root) / ".rsi"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Settings directory must not be a symlink")
    if value.api_key and value.api_key.get_secret_value().strip():
        key = value.api_key.get_secret_value().strip()
        if any(c in key for c in "\r\n\0"):
            raise ValueError("API Key 不能包含换行")
        temporary = directory / ("executor-key-" + uuid4().hex)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(key)
        os.replace(temporary, directory / "executor-key")
    write(directory / "executor-settings.json", value.model_dump(exclude={"api_key"}))
    pool = ModelPool(root)
    public = pool.public()
    scene = "executor_" + value.executor
    if value.model_id:
        pool.bind({scene: value.model_id})
    elif value.model or (value.api_key and value.api_key.get_secret_value().strip()):
        # Compatibility for existing callers; the browser edits models only in the pool.
        selected = public["bindings"].get(scene)
        entry = next((m for m in public["models"] if m["id"] == selected), None)
        data = {
            k: v for k, v in (entry or {}).items() if k not in {"id", "has_api_key"}
        }
        data.update(
            label=data.get("label", "DeepSeek"),
            name=value.model or data.get("name", "deepseek-flash"),
            api_key=value.api_key,
        )
        updated = pool.save(ModelInput(**data), selected)
        if not selected:
            pool.bind(
                {
                    "executor_bkcrab": updated["models"][-1]["id"],
                    "executor_dsh": updated["models"][-1]["id"],
                }
            )
    return catalog(root)


def builtin_profiles(root):
    current, native = catalog(root), runtime(root)
    if not current["configured"]:
        return []
    available = []
    for key in EXECUTORS:
        try:
            ModelPool(root).resolve("executor_" + key)
            available.append(key)
        except ValueError:
            pass
    return [
        {
            "id": "builtin-" + key,
            "name": name,
            "executor": key,
            "version": native["executors"][key],
            "evaluation": False,
            "evaluation_from_case": True,
            "publication": True,
        }
        for key, name in EXECUTORS.items()
        if key in native.get("executors", {}) and key in available
    ]


def builtin_profile(root, executor, target):
    root = Path(root).resolve()
    if executor not in EXECUTORS or target not in TARGETS:
        raise ValueError("不支持的执行 Agent 或目标 Agent")
    current, native = settings(root), runtime(root)
    if not current or executor not in native.get("executors", {}):
        raise ValueError("请先在页面保存执行 Agent 设置；服务器需安装原生运行环境")
    pool = ModelPool(root)
    bindings = pool.public()["bindings"]
    model = pool.resolve("improvement" if bindings.get("improvement") else "executor_" + executor)
    diagnostic = pool.resolve("diagnosis") if bindings.get("diagnosis") else dict(model)
    if not model.get("policy"):
        model["policy"] = {"protocols": ["anthropic-messages"] if executor == "dsh" else ["openai-chat"]}
    if not diagnostic.get("policy"):
        diagnostic["policy"] = {"protocols": ["anthropic-messages"] if executor == "dsh" else ["openai-chat"]}
    repository = root.parent / TARGETS[target]
    if not (repository / ".git").exists():
        raise ValueError("服务器未找到该 Agent 的源码仓库")
    script = str(Path(__file__).with_name("native_adapter.py"))
    common = [
        sys.executable,
        script,
        "--image",
        native["image"],
        "--timeout",
        str(current.timeout_seconds),
    ]
    origin = subprocess.run(
        ["git", "-C", str(repository), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    match = re.fullmatch(
        r"(?:https://github.com/|git@github.com:)([\w.-]+/[\w.-]+?)(?:\.git)?\s*",
        origin.stdout,
    )
    return Profile.model_validate(
        {
            "repository": str(repository),
            "parent_ref": "HEAD",
            "adapter": {
                "argv": common
                + [
                    "--root",
                    str(root),
                    "--executor",
                    executor,
                    "--model",
                    model["name"],
                    "--api-base",
                    model["api_base"],
                    "--messages-base",
                    model["messages_base"] or "",
                    "--key-ref",
                    model["api_key_env"].removeprefix("EVAL_POOL_KEY_"),
                    "--max-tokens",
                    str(model["max_tokens"]),
                    "--context-window",
                    str(model["context_window"]),
                    "--model-policy-json",
                    json.dumps(model, sort_keys=True),
                    "--diagnosis-model-policy-json",
                    json.dumps(diagnostic, sort_keys=True),
                ],
                "version": f"native-rsi/v2;{executor}={native['executors'][executor]};model={model['name']};pool={model['pool_id']}",
                "timeout_seconds": current.timeout_seconds + 60,
                "pass_env": ["DEEPSEEK_API_KEY"],
            },
            "checks": [
                {
                    "argv": common
                    + ["--check-target", target, "--workspace", "{workspace}"],
                    "timeout_seconds": current.timeout_seconds + 60,
                }
            ],
            "github_repository": match[1] if match else None,
        }
    )
