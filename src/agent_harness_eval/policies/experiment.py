from pathlib import Path

from ..workspace.models import identity
from .model import freeze as freeze_model
from .network import freeze as freeze_network


def fingerprint(config):
    model = config["model"]
    policy = freeze_model(model, "agent") if model.get("policy") else None
    network = freeze_network([Path(path) for path in config["benchmark"]["tasks"]],
        search={agent["id"]: agent.get("kwargs", {}).get("web_search") for agent in config["agents"]},
        review=model.get("search_review"))
    value = {"model": policy.model_dump(mode="json") if policy else None,
             "model_policy_digest": policy.digest if policy else None, "network": network,
             "execution": {key: config.get(key) for key in ("cpus", "memory_mb", "timeout_seconds", "attempts", "guardian")},
             "coverage": "配置指纹；不是实际网络与子模型路径已经验证的声明"}
    return {**value, "digest": identity(value)}
