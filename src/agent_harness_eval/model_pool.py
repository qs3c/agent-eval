"""Workspace model catalog; immutable credential references keep old runs stable."""

import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from .policies.model import Settings as ModelPolicySettings

SCENES = {
    "executor_bkcrab": "执行 Agent · BKCrab",
    "executor_dsh": "执行 Agent · DSH",
    "experiment": "实验 Agent",
    "guardian": "守护 Agent",
    "integration": "接入 Agent",
    "diagnosis": "诊断 Agent",
    "improvement": "改进 Agent",
    "user_simulator": "Benchmark 用户模拟器",
    "official_grading": "Benchmark 模型评分器",
}


def validate_endpoint(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(c.isspace() for c in value)
    ):
        raise ValueError("接口地址需为 HTTP(S) URL，不能包含密钥、查询参数或片段")
    return value.rstrip("/")


class ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$")
    api_base: str = Field(default="https://api.deepseek.com", max_length=500)
    messages_base: str | None = Field(default=None, max_length=500)
    max_tokens: int = Field(default=32768, gt=0, le=1048576)
    context_window: int = Field(default=1000000, gt=0, le=10000000)
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    policy: ModelPolicySettings | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def valid(self):
        self.label = self.label.strip()
        if not self.label:
            raise ValueError("请填写显示名称")
        self.api_base = validate_endpoint(self.api_base)
        if self.messages_base:
            self.messages_base = validate_endpoint(self.messages_base)
        elif self.api_base.removesuffix("/v1") == "https://api.deepseek.com":
            self.messages_base = "https://api.deepseek.com/anthropic"
        else:
            self.messages_base = None
        if self.context_window <= self.max_tokens:
            raise ValueError("上下文窗口必须大于最大输出 Token")
        if self.api_key and any(c in self.api_key.get_secret_value() for c in "\r\n\0"):
            raise ValueError("API Key 不能包含换行")
        return self


def legacy_key(root):
    root = Path(root)
    path = root / ".rsi/executor-key"
    if path.is_file() and not path.is_symlink() and path.read_text().strip():
        return path.read_text().strip()
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    for directory in [root, root.parent / "agent-harness-eval"]:
        env = directory / ".env"
        if env.is_file() and not env.is_symlink():
            for line in env.read_text().splitlines():
                name, _, value = line.removeprefix("export ").partition("=")
                if name.strip() == "DEEPSEEK_API_KEY":
                    return value.strip().strip("\"'")
    return ""


class ModelPool:
    def __init__(self, root):
        self.root = Path(root)
        self.state = self.root / ".console"
        self.state.mkdir(parents=True, exist_ok=True)
        self.keys = self.state / "model-keys"
        self.keys.mkdir(mode=0o700, exist_ok=True)
        self.path = self.state / "models.json"

    @contextmanager
    def locked(self):
        with (self.state / "models.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self):
        return (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {"models": [], "bindings": {}}
        )

    def _write(self, data):
        temp = self.state / ("models-" + uuid4().hex + ".tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, self.path)

    def _key(self, value):
        ref = uuid4().hex
        fd = os.open(self.keys / ref, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
        return ref

    def credential(self, ref):
        if not re.fullmatch(r"[0-9a-f]{32}", ref or ""):
            raise ValueError("无效的模型凭据引用")
        path = self.keys / ref
        if path.is_symlink() or not path.is_file():
            raise ValueError("模型凭据不存在，请到模型设置重新配置")
        return path.read_text()

    def migrate(self):
        with self.locked():
            if self.path.exists():
                return
            data = {"models": [], "bindings": {}}
            old = self.root / ".rsi/executor-settings.json"
            key = legacy_key(self.root)
            if old.is_file() or key:
                settings = json.loads(old.read_text()) if old.is_file() else {}
                value = ModelInput(
                    label="DeepSeek · 现有配置",
                    name=settings.get("model") or "deepseek-flash",
                )
                entry = {
                    "id": uuid4().hex,
                    **value.model_dump(exclude={"api_key"}),
                    "key_ref": self._key(key) if key else None,
                }
                data["models"].append(entry)
                data["bindings"] = {scene: entry["id"] for scene in SCENES}
            self._write(data)

    def public(self):
        self.migrate()
        data = self._read()
        return {
            "models": [
                {
                    **{k: v for k, v in m.items() if k != "key_ref"},
                    "has_api_key": bool(m.get("key_ref")),
                }
                for m in data["models"]
            ],
            "bindings": data["bindings"],
            "scenes": SCENES,
        }

    def save(self, value, identity=None):
        self.migrate()
        with self.locked():
            data = self._read()
            previous = next((m for m in data["models"] if m["id"] == identity), None)
            if identity and previous is None:
                raise ValueError("模型条目不存在")
            key = value.api_key.get_secret_value().strip() if value.api_key else ""
            ref = self._key(key) if key else (previous or {}).get("key_ref")
            if not ref:
                raise ValueError("新模型需要填写 API Key")
            entry = {
                "id": identity or uuid4().hex,
                **value.model_dump(exclude={"api_key"}),
                "key_ref": ref,
            }
            if previous:
                data["models"][data["models"].index(previous)] = entry
            else:
                data["models"].append(entry)
            if (
                any(
                    scene == "executor_dsh" and selected == entry["id"]
                    for scene, selected in data["bindings"].items()
                )
                and not entry["messages_base"]
            ):
                raise ValueError("DSH 默认模型需要 Messages 接口地址")
            self._write(data)
        return self.public()

    def bind(self, bindings):
        self.migrate()
        if set(bindings) - set(SCENES):
            raise ValueError("未知模型使用场景")
        with self.locked():
            data = self._read()
            for scene, identity in bindings.items():
                entry = next((m for m in data["models"] if m["id"] == identity), None)
                if identity is not None and not entry:
                    raise ValueError("所选模型不存在")
                if scene == "executor_dsh" and entry and not entry["messages_base"]:
                    raise ValueError("DSH 需要支持 Messages 的模型接口")
                data["bindings"][scene] = identity
            self._write(data)
        return self.public()

    def delete(self, identity):
        self.migrate()
        with self.locked():
            data = self._read()
            if identity in data["bindings"].values():
                raise ValueError("请先更换引用此模型的场景默认模型")
            if not any(m["id"] == identity for m in data["models"]):
                raise ValueError("模型条目不存在")
            data["models"] = [m for m in data["models"] if m["id"] != identity]
            self._write(data)
        return self.public()

    def resolve(self, scene, identity=None):
        self.migrate()
        data = self._read()
        identity = identity or data["bindings"].get(scene)
        entry = next((m for m in data["models"] if m["id"] == identity), None)
        if not entry or not entry.get("key_ref"):
            raise ValueError("请先在全局模型设置中添加模型并指定场景默认模型")
        if scene == "executor_dsh" and not entry["messages_base"]:
            raise ValueError("DSH 需要支持 Messages 的模型接口")
        result = {
            "mode": "live",
            "spending": "unbounded",
            "name": entry["name"],
            "api_base": entry["api_base"],
            "messages_base": entry["messages_base"],
            "max_tokens": entry["max_tokens"],
            "context_window": entry["context_window"],
            "api_key_env": "EVAL_POOL_KEY_" + entry["key_ref"],
            "pool_id": entry["id"],
        }
        if entry.get("policy"):
            result["policy"] = entry["policy"]
        return result

    def frozen_policy(self, role, identity=None):
        from .policies.model import freeze

        scene = "experiment" if role == "agent" else role
        return freeze(self.resolve(scene, identity), role)

    def environment(self, *configs):
        result = {}
        for config in configs:
            name = (config or {}).get("api_key_env", "")
            if name.startswith("EVAL_POOL_KEY_"):
                result[name] = self.credential(name.removeprefix("EVAL_POOL_KEY_"))
        return result

    def redact(self, text):
        for path in self.keys.iterdir():
            if (
                re.fullmatch(r"[0-9a-f]{32}", path.name)
                and path.is_file()
                and not path.is_symlink()
            ):
                value = path.read_text()
                if value:
                    text = text.replace(value, "[REDACTED]")
        return text


def router(root):
    from fastapi import APIRouter

    routes = APIRouter(prefix="/api")
    pool = ModelPool(root)

    @routes.get("/models")
    def overview():
        return pool.public()

    @routes.post("/models", status_code=201)
    def create(value: ModelInput):
        return pool.save(value)

    @routes.put("/models/{identity}")
    def update(identity: str, value: ModelInput):
        return pool.save(value, identity)

    @routes.delete("/models/{identity}")
    def delete(identity: str):
        return pool.delete(identity)

    @routes.put("/model-bindings")
    def bindings(value: dict[str, str | None]):
        return pool.bind(value)

    return routes
