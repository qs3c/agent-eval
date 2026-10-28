"""Experiment contract; agent and benchmark choices remain orthogonal."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .guardian.config import GuardianConfig
from .policies.model import Settings as ModelPolicySettings


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchReview(Strict):
    endpoint: str
    model: str = Field(pattern=r"^(typesafe/)?jev-[0-9]+\.[0-9]+(\.[0-9]+|-[0-9]{8})$")
    api_key_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout_seconds: float = Field(default=10, gt=0, le=60)
    deny_threshold: float = Field(default=0.5, gt=0, le=1)

    @model_validator(mode="after")
    def validate_endpoint(self):
        from urllib.parse import urlsplit

        url = urlsplit(self.endpoint)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None or url.query or url.fragment
                or any(c.isspace() for c in self.endpoint)):
            raise ValueError("审查接口需为不含凭据、查询参数或片段的 HTTP(S) URL")
        _ = url.port
        return self


class Model(Strict):
    policy: ModelPolicySettings | None = Field(default=None, exclude_if=lambda value: value is None)
    mode: Literal["offline", "mock", "live"] = "offline"
    name: str = "mock"
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    messages_base: str | None = None
    pool_id: str | None = None
    spending: Literal["disabled", "unbounded"] = "disabled"
    max_tokens: int = Field(default=8192, gt=0)
    context_window: int = Field(default=131072, gt=0)
    search_review: SearchReview | None = None

    @model_validator(mode="after")
    def validate_search_review(self):
        if self.search_review and self.mode != "live":
            raise ValueError("搜索审查仅支持 live 模式")
        if self.policy:
            from .policies.model import freeze

            freeze(self.model_dump(mode="json"))
            if self.search_review:
                raise ValueError("固定模型策略当前不接管辅助搜索；请使用保留的搜索策略入口，不能把它认作完整模型策略覆盖")
        return self


class Agent(Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    implementation: str
    version: str
    kwargs: dict = Field(default_factory=dict)
    capabilities: set[str] = Field(default_factory=lambda: {"filesystem", "shell"})


class DatasetIdentity(Strict):
    name: str = Field(min_length=1)
    revision: str | None = Field(default=None, min_length=1)
    split: str | None = Field(default=None, min_length=1)


class EvaluationIdentity(Strict):
    name: str = Field(min_length=1)
    version: str | None = Field(default=None, min_length=1)
    environment: str | None = Field(default=None, min_length=1)


class Benchmark(Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    plugin: Literal["harbor-tasks", "controls", "swe"] | None = Field(default=None, exclude_if=lambda value: value is None)
    tasks: list[Path] = Field(min_length=1)
    requires: set[str] = Field(default_factory=lambda: {"filesystem", "shell"})
    primary_metric: str = "reward"
    score_kind: Literal["metric", "resolved"] = "metric"
    dataset: DatasetIdentity | None = None
    evaluation: EvaluationIdentity | None = None


class BatchConfig(Strict):
    size: int = Field(default=20, ge=1, le=500)
    prepare_swe: bool = False
    official_regrade: bool = False
    retain_count: int = Field(default=3, ge=0, le=20)
    retain_gib: float = Field(default=20, gt=0)
    retain_hours: float = Field(default=24, gt=0)
    min_free_gib: float = Field(default=20, ge=0)
    max_pending_grade_batches: int = Field(default=2, ge=1, le=20)
    estimated_task_gib: float = Field(default=2, gt=0)


class Experiment(Strict):
    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    model: Model = Field(default_factory=Model)
    agents: list[Agent] = Field(min_length=1)
    benchmark: Benchmark
    attempts: int = Field(default=1, ge=1)
    concurrency: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(default=300, gt=0)
    setup_timeout_seconds: int = Field(default=600, gt=0)
    cpus: int = Field(default=2, gt=0)
    memory_mb: int = Field(default=4096, gt=0)
    guardian: GuardianConfig | None = None
    batch: BatchConfig | None = None

    @model_validator(mode="after")
    def validate_combinations(self):
        if self.batch:
            if self.guardian is None:
                self.guardian = GuardianConfig()
            if (
                self.batch.prepare_swe or self.batch.official_regrade
            ) and self.benchmark.score_kind != "resolved":
                raise ValueError(
                    "SWE batch preparation/regrading requires a resolved benchmark"
                )
        ids = [a.id for a in self.agents]
        if len(ids) != len(set(ids)):
            raise ValueError("agent ids must be unique")
        if len(self.benchmark.tasks) != len(set(self.benchmark.tasks)):
            raise ValueError("duplicate tasks")
        if self.model.context_window <= self.model.max_tokens:
            raise ValueError("context_window must exceed max_tokens")
        for agent in self.agents:
            missing = self.benchmark.requires - agent.capabilities
            if missing:
                raise ValueError(
                    f"{agent.id}: incompatible task requirements: {sorted(missing)}"
                )
            reject_credentials(agent.kwargs)
            if agent.kwargs.get("web_search") is not None:
                from .search import WebSearch

                WebSearch.model_validate(agent.kwargs["web_search"])
                if agent.implementation.startswith("agent_harness_eval.agents."):
                    raise ValueError("web_search requires an external adapter that implements the search contract")
                if self.model.mode != "live":
                    raise ValueError("external web_search requires live mode")
                if self.model.policy:
                    raise ValueError("固定模型策略当前不支持辅助搜索；不能静默绕过模型请求预算")
            if "sdk-minimal" in str(agent.kwargs):
                raise ValueError("sdk-minimal is excluded from this comparison")
        return self


def reject_credentials(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if any(
                s in key.lower()
                for s in ("api_key", "password", "secret", "authorization")
            ):
                raise ValueError(
                    "credentials belong in environment variables, not agent kwargs"
                )
            reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            reject_credentials(child)


def canonical_configuration(value):
    """Stable input representation; ordered task/agent selections stay ordered."""
    from .workspace.models import normalize

    return normalize(value)


def load(path: Path, *, validate_agent_artifacts=True) -> Experiment:
    exp = Experiment.model_validate_json(path.read_text())
    exp.benchmark.tasks = [(path.parent / t).resolve() for t in exp.benchmark.tasks]
    from .benchmarks.registry import resolve

    resolve(exp.benchmark, batch=exp.batch).validate(exp.benchmark)
    names = [t.name for t in exp.benchmark.tasks]
    if len(set(names)) != len(names):
        raise ValueError("task directory names must be unique in an experiment")
    for task in exp.benchmark.tasks:
        if not (task / "task.toml").is_file():
            raise ValueError(f"not a Harbor task: {task}")
        from harbor.models.task.config import TaskConfig

        config = TaskConfig.model_validate_toml((task / "task.toml").read_text())
        if not (exp.batch and exp.batch.prepare_swe):
            from .search import search_hosts

            hosts = search_hosts(exp)
            if hosts and (
                config.agent.network_mode.value != "allowlist"
                or not set(hosts) <= set(config.agent.allowed_hosts)
            ):
                raise ValueError("web_search endpoints must be explicitly included in task network allowlist")
        if exp.model.mode == "mock" and config.agent.network_mode.value != "no-network":
            raise ValueError(
                "mock tasks must explicitly set agent.network_mode = no-network"
            )
    for agent in exp.agents:
        for key in ("bundle", "binary", "provenance"):
            if key in agent.kwargs:
                agent.kwargs[key] = str((path.parent / agent.kwargs[key]).resolve())
                if validate_agent_artifacts and not Path(agent.kwargs[key]).is_file():
                    raise ValueError(f"missing agent artifact: {agent.kwargs[key]}")
        if exp.model.policy:
            protocol = "anthropic-messages" if agent.implementation == "agent_harness_eval.agents.dsh:DeepSeekStandard" else "openai-chat"
            if agent.implementation == "agent_harness_eval.agents.recipe:Recipe" and Path(agent.kwargs.get("provenance", "")).is_file():
                import json

                protocol = json.loads(Path(agent.kwargs["provenance"]).read_text())["recipe"]["model_protocol"]
            if protocol != "none" and protocol not in exp.model.policy.protocols:
                raise ValueError(f"{agent.id}: model policy does not admit required protocol {protocol}")
    return exp
