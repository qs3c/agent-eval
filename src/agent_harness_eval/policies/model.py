import json
from typing import Literal

from pydantic import Field, model_validator

from ..workspace.models import Frozen, identity
from .budget import Budget

Role = Literal["agent", "integration", "guardian", "diagnosis", "improvement", "user_simulator", "official_grading"]
PARAMETERS = {"temperature", "top_p", "seed", "reasoning_effort", "thinking", "output_config"}


class Settings(Frozen):
    parameters: dict = Field(default_factory=dict)
    protocols: tuple[Literal["openai-chat", "anthropic-messages"], ...] = ("openai-chat",)
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode="after")
    def validate_parameters(self):
        if set(self.parameters) - PARAMETERS:
            raise ValueError("Unsupported fixed model parameter")
        json.dumps(self.parameters, allow_nan=False)
        for key, minimum, maximum in (("temperature", 0, 2), ("top_p", 0, 1)):
            if key in self.parameters:
                value = self.parameters[key]
                if type(value) not in (int, float) or not minimum <= value <= maximum or key == "top_p" and value == 0:
                    raise ValueError("Invalid fixed sampling parameter: " + key)
        if "seed" in self.parameters and type(self.parameters["seed"]) is not int:
            raise ValueError("seed must be an integer")
        if "reasoning_effort" in self.parameters and not isinstance(self.parameters["reasoning_effort"], str):
            raise ValueError("reasoning_effort must be a string")
        for key, allowed in (("thinking", {"type", "budget_tokens"}), ("output_config", {"effort"})):
            if key in self.parameters and (not isinstance(self.parameters[key], dict) or set(self.parameters[key]) - allowed):
                raise ValueError("Invalid fixed reasoning parameter: " + key)
        if not self.protocols or len(set(self.protocols)) != len(self.protocols):
            raise ValueError("Choose distinct supported protocols")
        return self


class ModelPolicy(Frozen):
    role: Role
    model: dict
    settings: Settings

    @property
    def digest(self):
        return identity(self)


def freeze(profile, role: Role = "agent"):
    from urllib.parse import urlsplit

    profile = profile.model_dump(mode="json") if hasattr(profile, "model_dump") else json.loads(json.dumps(profile))
    for key in ("api_base", "messages_base"):
        if profile.get(key):
            url = urlsplit(profile[key])
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("Model endpoints must not embed credentials or query parameters")
    if not profile.get("messages_base") and profile.get("api_base", "").rstrip("/").removesuffix("/v1") == "https://api.deepseek.com":
        profile["messages_base"] = "https://api.deepseek.com/anthropic"
    settings = Settings.model_validate(profile.get("policy") or {})
    if "anthropic-messages" in settings.protocols and not profile.get("messages_base") and profile.get("mode") != "mock":
        raise ValueError("Messages policy requires a configured endpoint")
    model = {key: profile.get(key) for key in ("name", "api_base", "messages_base", "api_key_env", "pool_id", "max_tokens", "context_window", "mode")}
    return ModelPolicy(role=role, model=model, settings=settings)


def verify_usage(policy, evidence):
    """Report observed consistency separately from unobserved network/role paths."""
    issues = []
    requests = evidence.get("requests", [])
    if not requests:
        issues.append("没有实际模型请求证据")
    for row in requests:
        body = row.get("body", {})
        if row.get("policy_digest") != policy.digest or row.get("role") != policy.role:
            issues.append("调用缺少匹配的可信策略/角色标识")
        if body.get("model") != policy.model["name"] or body.get("max_tokens") != policy.model["max_tokens"]:
            issues.append("实际模型或输出限制不一致")
        if any(body.get(key) != value for key, value in policy.settings.parameters.items()):
            issues.append("实际模型参数不一致")
    observed_consistent = bool(requests) and not issues
    coverage = evidence.get("coverage", {})
    if coverage.get("network_enforced") is not True:
        issues.append("网络旁路覆盖未验证")
    if policy.role == "agent" and set(coverage.get("tested_paths", [])) != {"primary", "subagent", "compression", "fallback"}:
        issues.append("主/子 Agent、压缩与回退路径覆盖不完整")
    if not policy.settings.parameters:
        issues.append("未指定统一采样/推理参数")
    return {"policy_digest": policy.digest, "passed": not issues, "reasons": sorted(set(issues)),
            "observed_requests": len(requests), "observed_consistent": observed_consistent,
            "cost_usd": evidence.get("cost_usd")}
