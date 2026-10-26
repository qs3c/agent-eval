"""Bounded local version/evaluation cycles; never merge or publish implicitly."""
import json
import re
import time
from pathlib import Path
from uuid import uuid4

from ..guardian.evidence import read, write
from ..operations.commands import Commands, TERMINAL
from ..operations.manager import exclusive
from ..integrations.sources import Sources, repository
from ..integrations.store import Releases
from ..provenance import digest, tree_manifest


def directory(root):
    return Path(root).resolve() / ".rsi/cycles"


def path(root, key):
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise ValueError("Invalid cycle identity")
    target = directory(root) / key
    if target.is_symlink():
        raise ValueError("Cycle directory must not be a symlink")
    return target


def get(root, key):
    value = read(path(root, key) / "record.json")
    if not value:
        raise KeyError(key)
    return value


def list_cycles(root):
    if not directory(root).exists():
        return []
    return [get(root, p.name) for p in sorted(directory(root).iterdir()) if p.is_dir() and re.fullmatch(r"[0-9a-f]{32}", p.name)]


def save(root, value):
    value["updated"] = time.time()
    write(path(root, value["id"]) / "record.json", value)
    return value


def create(root, source_id, evaluation, *, candidate=None, max_rounds=1, max_attempts=20, max_seconds=86400, confirm_paid=False):
    from ..config import load
    from ..freezing import freeze_selection, freeze_tree
    from .store import Store

    if not 1 <= max_rounds <= 100 or not 2 <= max_attempts <= 10000 or not 60 <= max_seconds <= 604800:
        raise ValueError("Cycle limits are out of range")
    sources = Sources(root)
    source = sources.get(source_id)
    baseline = sources.round_release(source_id)
    exp = load(Path(evaluation), validate_agent_artifacts=False)
    if exp.guardian or exp.batch:
        raise ValueError("自动质量复评只接受 autonomous 配置")
    if exp.model.mode == "live" and not confirm_paid:
        raise ValueError("启动真实模型闭环需要确认模型费用；美元成本可能未知")
    if exp.model.mode == "live" and (not exp.model.policy or exp.model.policy.budget.max_requests > 1000):
        raise ValueError("闭环需要显式模型策略和每次尝试最多 1000 次请求预算")
    attempts = len(exp.benchmark.tasks) * exp.attempts * 2
    if attempts > max_attempts:
        raise ValueError("首轮已超过尝试预算")
    pr = None
    if candidate:
        record = Store(root).get("candidates", candidate)
        pr = record.get("pr_url")
        if not pr or repository("https://github.com/" + record["profile"].get("github_repository", "")) != source["repository"]:
            raise ValueError("候选需要同仓库已发布 PR；发布仍是单独的显式操作")
    key = uuid4().hex
    dest = path(root, key)
    dest.mkdir(parents=True)
    config = exp.model_dump(mode="json")
    freeze_selection(config["benchmark"]["tasks"], dest / "tasks")
    for index, task in enumerate(config["benchmark"]["tasks"]):
        old = Path(task)
        target = dest / "tasks" / old.name
        freeze_tree(old, target, dest / "shared-runtime")
        config["benchmark"]["tasks"][index] = str(target)
    config["agents"] = [baseline]
    from ..local_flow import validate_agents

    validate_agents(config)
    write(dest / "evaluation.json", config)
    return save(root, {"id": key, "source": source_id, "candidate": candidate, "pr_url": pr,
        "state": "waiting_merge" if pr else "waiting_update", "reason": "等待合并" if pr else "等待来源的新准入版本",
        "baseline": baseline["version"], "last_release": baseline["version"], "rounds": [],
        "max_rounds": max_rounds, "max_attempts": max_attempts, "max_seconds": max_seconds,
        "created": time.time(), "next_poll": 0, "refresh_job": None, "sequence": 0,
        "config_digest": digest(dest / "evaluation.json"),
        "task_hashes": {p: tree_manifest(Path(p))["sha256"] for p in config["benchmark"]["tasks"]}})


def queue(root, key):
    with exclusive(path(root, key) / "cycle.lock"):
        value = get(root, key)
        commands = Commands(root)
        if value["refresh_job"]:
            previous = commands.query(value["refresh_job"])
            if previous["status"] not in TERMINAL:
                return previous
        sequence = value["sequence"] + 1
        job = commands.submit("cycle-refresh", {"cycle_id": key}, key=f"cycle:{key}:{sequence}")
        value.update(refresh_job=job["id"], sequence=sequence, next_poll=time.time() + 60)
        save(root, value)
        return job


def tick(root):
    for value in list_cycles(root):
        if value["state"] in {"stopped", "completed", "failed", "waiting_diagnosis"}:
            continue
        if value["next_poll"] <= time.time():
            queue(root, value["id"])


def enforce_deadlines(root):
    # Also called while the single execution worker is busy, so a long running
    # experiment cannot defer enforcement until the next queued refresh.
    for value in list_cycles(root):
        if value["state"] not in {"stopped", "completed", "failed", "waiting_diagnosis"} and time.time() - value["created"] >= value["max_seconds"]:
            stop(root, value["id"], reason="达到闭环时间上限；取消尚未完成的本轮实验")


def stop(root, key, *, reason="用户停止；历史证据保留"):
    with exclusive(path(root, key) / "cycle.lock"):
        value = get(root, key)
        for round in value["rounds"]:
            if round.get("job") and Commands(root).query(round["job"])["status"] not in TERMINAL:
                Commands(root).control(round["job"], "cancel")
        value.update(state="stopped", reason=reason)
        return save(root, value)


def quality(root, value, round):
    """Re-read the actual run, require complete paired scores and no assistance."""
    job = Commands(root).query(round["job"])
    if job["status"] != "completed":
        return {"assessment": "inconclusive", "reason": "实验未正常完成"}
    output = Path(job["payload"]["output"])
    from ..report import report

    report(output)
    result = read(output / "report.json", {})
    manifest = read(output / "manifest.json", {})
    config = manifest.get("experiment", {})
    if config.get("guardian") or config.get("batch") or list(output.glob("jobs/*/*/guardian/assisted.json")):
        return {"assessment": "inconclusive", "reason": "实验包含辅助干预"}
    expected = {"baseline": value["baseline"], "candidate": round["release"]}
    if {a["id"]: a["version"] for a in config.get("agents", [])} != expected:
        raise ValueError("Round Release identity changed")
    rows = result.get("agents", {})
    if result.get("report_errors") or any(not (r := rows.get(side, {})) or r.get("errors") or r.get("missing") or r.get("scored") != r.get("expected") or r.get("mean_on_scored") is None for side in expected):
        return {"assessment": "inconclusive", "reason": "缺失、失败或未评分尝试不能判定改善"}
    delta = rows["candidate"]["mean_on_scored"] - rows["baseline"]["mean_on_scored"]
    assessment = "improved" if delta > 0 else "equal" if delta == 0 else "regressed"
    # Mock/offline controls measure integration, never model/harness quality.
    return {"assessment": assessment, "mode": config["model"]["mode"], "delta": delta,
        "evidence": str(output), "report_digest": digest(output / "report.json"),
        "recommendable": config["model"]["mode"] == "live" and assessment in {"equal", "improved"},
        "limitations": ["结论仅限冻结题目、模型与运行条件；不表示总体显著提升或完整可比性认证"]}


def refresh(root, key, *, observer=None):
    from .merge_tracking import observe, invalidate_drift
    from .store import Store
    from ..integrations.tracking import refresh as refresh_source, ensure_build
    from ..operations.submission import submit_experiment
    from ..config import load, Agent

    observer = observer or observe
    with exclusive(path(root, key) / "cycle.lock"):
        value = get(root, key)
        if value["state"] in {"completed", "stopped", "failed", "waiting_diagnosis"}:
            return value
        if time.time() - value["created"] >= value["max_seconds"]:
            for round in value["rounds"]:
                if round.get("job") and Commands(root).query(round["job"])["status"] not in TERMINAL:
                    Commands(root).control(round["job"], "cancel")
            value.update(state="stopped", reason="达到闭环时间上限；不再提交新工作")
            return save(root, value)
        dest = path(root, key)
        if digest(dest / "evaluation.json") != value["config_digest"] or any(tree_manifest(Path(p))["sha256"] != h for p, h in value["task_hashes"].items()):
            raise ValueError("Frozen cycle conditions changed")
        sources = Sources(root)
        source = sources.get(value["source"])
        if value["state"] == "evaluating":
            round = value["rounds"][-1]
            if Commands(root).query(round["job"])["status"] not in TERMINAL:
                return value
            round["quality"] = quality(root, value, round)
            value["last_release"] = round["release"]
            if round["quality"].get("recommendable"):
                with sources.transaction() as db:
                    db.execute("UPDATE sources SET recommended=? WHERE id=?", (round["release"], source["id"]))
                    sources.event(db, source["id"], "recommended", {"cycle": key, "round": round})
            if round["quality"]["assessment"] in {"regressed", "inconclusive"}:
                value.update(state="waiting_diagnosis", reason="复评退化或证据不足；停止自动推进，保留问题供 RSI 诊断")
            elif len(value["rounds"]) >= value["max_rounds"]:
                value.update(state="completed", reason="达到设定轮次；质量结论见逐轮证据")
            elif value["pr_url"]:
                value.update(state="waiting_diagnosis", reason="合并后复评完成；下一次诊断与 PR 发布需显式发起")
            else:
                value.update(state="waiting_update", reason="等待下一源码版本；本轮证据已冻结")
            return save(root, value)
        if value["pr_url"]:
            observation = observer(value["pr_url"])
            if observation["repository"] != source["repository"] or observation["base_ref"] != source["ref"].removeprefix("refs/heads/"):
                raise ValueError("PR destination differs from tracked source")
            value["observation"] = observation
            record = Store(root).get("candidates", value["candidate"])
            stale = invalidate_drift(Store(root), record, observation)
            if observation["state"] == "closed":
                value.update(state="stopped", reason="PR 已关闭且未合并")
                return save(root, value)
            if observation["state"] == "open":
                value.update(state="waiting_merge", reason="基线或候选已漂移，需要更新候选并重新验证" if stale else "等待用户合并 PR")
                return save(root, value)
            sources.pin_merge(source["id"], observation["merge_sha"])
            ensure_build(root, {**source, "observed": observation["merge_sha"]})
            refresh_source(root, source["id"], observe=False)
            revision = next(r for r in sources.revisions(source["id"]) if r["sha"] == observation["merge_sha"])
            target = revision["release"] if revision["status"] == "admitted" else None
        else:
            refresh_source(root, source["id"])
            source = sources.get(source["id"])
            revision = next((r for r in sources.revisions(source["id"]) if r["sha"] == source["observed"]), {})
            target = source["admitted"] if revision.get("status") == "admitted" else None
        if revision.get("status") == "integration_failed":
            value.update(state="failed", reason="新源码接入失败；旧 Release 保留，修复配方后可启动新闭环")
            return save(root, value)
        if not target or target == value["last_release"]:
            value.update(state="waiting_admission" if not target else "waiting_update", reason="等待新版本通过构建与运行检查")
            return save(root, value)
        exp = load(dest / "evaluation.json", validate_agent_artifacts=False)
        attempts = len(exp.benchmark.tasks) * exp.attempts * 2
        if len(value["rounds"]) >= value["max_rounds"] or sum(r["attempts"] for r in value["rounds"]) + attempts > value["max_attempts"]:
            value.update(state="stopped", reason="达到轮次或尝试预算上限")
            return save(root, value)
        catalog = Releases(root)
        if any((catalog.directory / release / "disabled.json").exists() for release in (value["baseline"], target)):
            value.update(state="stopped", reason="基线或候选 Release 已禁用；不提交新的复评")
            return save(root, value)
        exp.agents = [Agent.model_validate({**catalog.agent(release), "id": name}) for name, release in (("baseline", value["baseline"]), ("candidate", target))]
        from ..local_flow import validate_agents

        validate_agents(exp.model_dump(mode="json"))
        output = dest / ("round-" + str(len(value["rounds"]) + 1))
        existing = next((j for j in Commands(root).list() if j["kind"] == "experiment" and j["payload"].get("output") == str(output.resolve())), None)
        job = existing or submit_experiment(root, exp, output)
        value["rounds"].append({"release": target, "source_sha": revision["sha"], "baseline": value["baseline"],
            "job": job["id"], "plan_id": job["payload"]["plan_id"], "attempts": attempts})
        value.update(state="evaluating", reason="使用新准入版本与冻结基线进行同条件复评")
        return save(root, value)


def perform(root, job_id):
    from ..operations.worker import is_managed_worker

    if not is_managed_worker():
        raise ValueError("Cycle refresh requires Manager ownership")
    key = Commands(root).query(job_id)["payload"]["cycle_id"]
    try:
        return refresh(root, key)
    except Exception as error:
        # No external writes occurred. Retry observations after backoff, never PR creation.
        with exclusive(path(root, key) / "cycle.lock"):
            value = get(root, key)
            value.update(reason="闭环查询/验证失败：" + str(error), next_poll=time.time() + 300)
            save(root, value)
        raise
