"""Public native executors and explicit, user-owned target profiles."""
import json
import sys
from pathlib import Path
from typing import Literal
from pydantic import Field, SecretStr
from ..guardian.evidence import read, write
from ..model_pool import ModelInput, ModelPool, legacy_key
from ..platform_agents.releases import EXECUTORS, catalog as release_catalog, resolve, runtime as release_runtime
from .models import Strict, load_profile

class ExecutorSettings(Strict):
    executor: Literal["opencode", "dsh", "hermes"]
    model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$")
    model_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    timeout_seconds: int = Field(default=1800, ge=60, le=7200)
    api_key: SecretStr | None = Field(default=None, max_length=4096)

def credential(root):
    return legacy_key(root)

def runtime(root):
    native = release_runtime(root)
    if not native:
        return {}
    return {**native, "executors": {o["id"]: o["release_id"] for o in release_catalog(root) if o["ready"]}}

def settings(root):
    value = read(Path(root) / ".rsi/executor-settings.json", {})
    # Retired local settings are retained on disk, never offered as public choices.
    return ExecutorSettings.model_validate(value) if value.get("executor") in EXECUTORS else None

def catalog(root):
    saved = settings(root)
    public = ModelPool(root).public()
    executor = saved.executor if saved else "dsh"
    selected = public["bindings"].get("executor_" + executor)
    entry = next((m for m in public["models"] if m["id"] == selected), None)
    options = release_catalog(root)
    return {"options": [{**o, "installed": o["ready"], "description": o["reason"]} for o in options],
        "configured": saved is not None, "executor": executor, "model": entry["name"] if entry else None,
        "model_id": selected, "models": public["models"], "bindings": public["bindings"],
        "timeout_seconds": saved.timeout_seconds if saved else 1800,
        "has_api_key": bool(entry and entry["has_api_key"]),
        "ready": bool(saved and entry and entry["has_api_key"] and any(o["id"] == executor and o["ready"] for o in options))}

def save_settings(root, value):
    pool = ModelPool(root)
    scene = "executor_" + value.executor
    if value.model_id:
        pool.resolve(scene, value.model_id)
        pool.bind({scene: value.model_id})
    elif value.api_key and value.api_key.get_secret_value().strip():
        updated = pool.save(ModelInput(label=value.executor, name=value.model or "deepseek-flash", api_key=value.api_key))
        pool.bind({scene: updated["models"][-1]["id"]})
    elif value.model:
        raise ValueError("请选择全局模型或提供模型凭据")
    directory = Path(root) / ".rsi"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Settings directory must not be a symlink")
    write(directory / "executor-settings.json", value.model_dump(exclude={"api_key"}))
    return catalog(root)

def builtin_profiles(root):
    if not settings(root):
        return []
    available = []
    for option in release_catalog(root):
        if not option["ready"]:
            continue
        try:
            ModelPool(root).resolve("executor_" + option["id"])
        except ValueError:
            continue
        available.append({"id": "builtin-" + option["id"], "name": option["name"],
            "executor": option["id"], "version": option["release_id"], "evaluation": False,
            "evaluation_from_case": True, "publication": True})
    return available

def builtin_profile(root, executor, target):
    root = Path(root).resolve()
    _, receipt = resolve(root, executor)
    current, native = settings(root), release_runtime(root)
    if not current or not native:
        raise ValueError("请先保存执行 Agent 设置并准备平台容器")
    # A target is a user profile, never an implicitly distributed personal repository.
    try:
        profile = load_profile(root, target)
    except FileNotFoundError as error:
        raise ValueError("请为被测 Agent 配置 rsi-profiles/<agent-id>.json，声明源码、配方与检查命令") from error
    pool = ModelPool(root)
    bindings = pool.public()["bindings"]
    model = pool.resolve("improvement" if bindings.get("improvement") else "executor_" + executor)
    diagnostic = pool.resolve("diagnosis") if bindings.get("diagnosis") else dict(model)
    for policy in (model, diagnostic):
        if not policy.get("policy"):
            policy["policy"] = {"protocols": [receipt["recipe"]["model_protocol"]]}
    from .models import Adapter
    profile.adapter = Adapter(argv=[sys.executable, str(Path(__file__).with_name("native_adapter.py")),
        "--root", str(root), "--executor", executor, "--release", receipt["release_id"],
        "--image", native["image"], "--timeout", str(current.timeout_seconds),
        "--model-policy-json", json.dumps(model), "--diagnosis-model-policy-json", json.dumps(diagnostic)],
        version="recipe-executor/" + receipt["release_id"], timeout_seconds=current.timeout_seconds + 60)
    return profile
