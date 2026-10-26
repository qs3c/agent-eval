"""Domain contracts and canonical identities shared by CLI, Web and workers."""

import hashlib
import json
import math
import re
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1


class SourceRevision(Frozen):
    repository: str
    ref: str | None = None
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")
    observed_at: str | None = None


class AgentRelease(Frozen):
    agent_id: str
    source: SourceRevision | None = None
    recipe_digest: str | None = None
    patch_digest: str | None = None
    artifact_digests: dict[str, str] = Field(default_factory=dict)
    runtime_digest: str | None = None
    profile: str | None = None
    provenance: Literal["unknown", "recorded"] = "unknown"


class BenchmarkRevision(Frozen):
    benchmark_id: str
    task_ids: tuple[str, ...]
    task_hashes: dict[str, str] = Field(default_factory=dict)
    dataset: dict | None = None
    scorer: dict | None = None
    selection: dict | None = None


class ExperimentPlan(Frozen):
    policies: dict | None = Field(default=None, exclude_if=lambda value: value is None)
    experiment: dict
    agents: tuple[AgentRelease, ...] = ()
    benchmark: BenchmarkRevision | None = None
    workbench: dict = Field(default_factory=dict)
    evidence: tuple[str, ...] = ()


class Attempt(Frozen):
    plan_id: str
    agent_id: str
    task_id: str
    repeat: int = Field(ge=1)
    release_id: str | None = None


class GradeRun(Frozen):
    attempt_id: str
    scorer: str
    input_digest: str
    ordinal: int = Field(default=1, ge=1)


class ResourceLease(Frozen):
    owner: str
    resource_id: str
    resource_type: str
    generation: int = Field(ge=1)
    consumers: tuple[str, ...] = ()


class Incident(Frozen):
    attempt_id: str
    kind: str
    evidence: tuple[str, ...]


class Execution(str, Enum):
    queued = "queued"
    preparing = "preparing"
    running = "running"
    paused = "paused"
    interrupted = "interrupted"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution: Execution = Execution.queued
    scoring: Literal["not_requested", "pending", "running", "complete", "partial", "failed"] = "not_requested"
    evidence: Literal["complete", "partial", "missing"] = "missing"
    resources: Literal["active", "retained", "reclaim_pending", "reclaimed", "cleanup_failed"] = "active"


def normalize(value, path=()):
    """Only contract-declared sets lose ordering. Credentials are rejected, not hashed."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("Canonical object keys must be strings")
            lowered = key.lower()
            reference = lowered.endswith(("_env", "_ref"))
            if not reference and re.search(r"(^|_)(api_key|password|secret|authorization|access_token|refresh_token|credential)(s)?($|_)", lowered):
                raise ValueError("Credentials must be references, never embedded in a plan")
            result[key] = normalize(child, (*path, key))
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [normalize(child, (*path, "*")) for child in value]
        declared_set = path[-3:] == ("agents", "*", "capabilities") or path[-2:] == ("benchmark", "requires")
        if isinstance(value, (set, frozenset)) or declared_set:
            return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=True))
        return items
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite values cannot be frozen")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Unsupported canonical value type")


def canonical_json(value):
    return json.dumps(normalize(value), sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def identity(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
