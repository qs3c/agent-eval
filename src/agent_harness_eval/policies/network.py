from ..workspace.models import identity


def freeze(tasks, *, search=None, review=None):
    from harbor.models.task.config import TaskConfig

    rows = []
    for task in tasks:
        config = TaskConfig.model_validate_toml((task / "task.toml").read_text())
        rows.append({"task": task.name, "mode": config.agent.network_mode.value if config.agent.network_mode else "unspecified",
                     "allowed_hosts": sorted(config.agent.allowed_hosts or [])})
    value = {"tasks": rows, "search": search, "review": review,
             "review_scope": "仅覆盖经过平台审查入口的搜索；普通浏览器或 shell 流量不自动受 JEV 审查"}
    return {**value, "digest": identity(value)}
