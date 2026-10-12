"""Experiment contract; agent and benchmark choices remain orthogonal."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .guardian.config import GuardianConfig


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Model(Strict):
    mode: Literal["offline", "mock", "live"] = "offline"
    name: str = "mock"
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    spending: Literal["disabled", "unbounded"] = "disabled"
    max_tokens: int = Field(default=8192, gt=0)
    context_window: int = Field(default=131072, gt=0)


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
    tasks: list[Path] = Field(min_length=1)
    requires: set[str] = Field(default_factory=lambda: {"filesystem", "shell"})
    primary_metric: str = "reward"
    score_kind: Literal["metric", "resolved"] = "metric"
    dataset: DatasetIdentity | None = None
    evaluation: EvaluationIdentity | None = None


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

    @model_validator(mode="after")
    def validate_combinations(self):
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


def load(path: Path) -> Experiment:
    exp = Experiment.model_validate_json(path.read_text())
    exp.benchmark.tasks = [(path.parent / t).resolve() for t in exp.benchmark.tasks]
    names = [t.name for t in exp.benchmark.tasks]
    if len(set(names)) != len(names):
        raise ValueError("task directory names must be unique in an experiment")
    for task in exp.benchmark.tasks:
        if not (task / "task.toml").is_file():
            raise ValueError(f"not a Harbor task: {task}")
        from harbor.models.task.config import TaskConfig

        config = TaskConfig.model_validate_toml((task / "task.toml").read_text())
        if exp.model.mode == "mock" and config.agent.network_mode.value != "no-network":
            raise ValueError(
                "mock tasks must explicitly set agent.network_mode = no-network"
            )
    for agent in exp.agents:
        for key in ("bundle", "binary", "provenance"):
            if key in agent.kwargs:
                agent.kwargs[key] = str((path.parent / agent.kwargs[key]).resolve())
                if not Path(agent.kwargs[key]).is_file():
                    raise ValueError(f"missing agent artifact: {agent.kwargs[key]}")
    return exp
