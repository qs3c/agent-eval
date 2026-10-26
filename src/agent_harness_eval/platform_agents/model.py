"""Trusted direct-model entry for the existing Guardian JSONL driver."""
import json

from ..guardian.evidence import append, write
from ..policies.model import freeze
from ..resources.policy_runtime import Counter, normalize


def guardian_profile(model):
    # Preserve the existing driver's tool-choice requirement as explicit policy.
    if model and not model.get("policy") and model.get("api_base", "https://api.deepseek.com").rstrip("/").removesuffix("/v1") == "https://api.deepseek.com":
        return {**model, "policy": {"parameters": {"thinking": {"type": "disabled"}}}}
    return model


def prepare(model, body, directory, role):
    directory.mkdir(parents=True, exist_ok=True)
    policy = freeze(model, role)
    record = {"policy": policy.model_dump(mode="json"), "digest": policy.digest}
    path = directory / "model-policy.json"
    if path.exists() and json.loads(path.read_text()) != record:
        raise ValueError("Platform model policy changed within a session")
    write(path, record)
    body = normalize(body, record["policy"], "openai-chat")
    if not Counter(directory / "model-budget.jsonl", policy.settings.budget.max_requests).reserve(role, policy.digest):
        raise RuntimeError("Platform model request budget exhausted")
    append(directory / "policy-requests.jsonl", {"role": role, "policy_digest": policy.digest, "body": body, "protocol": "openai-chat"})
    return body
