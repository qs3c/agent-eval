import math
import os
import shutil
from uuid import uuid4

from ..config import load
from ..guardian.config import GuardianConfig
from ..guardian.evidence import now, read, write
from ..manifests import load_manifest
from ..provenance import digest, tree_manifest, validate_tree
from ..run import run_experiment
from .git import assert_clean, clone
from .native_evaluation import configuration
from .process import checked


def compare(parent, candidate):
    a, b = load_manifest(parent), load_manifest(candidate)
    reports = [read(path / "report.json", {}) for path in (parent, candidate)]
    problems = []
    from ..measurement.results import official_trials, uses_official_swe

    for output, manifest, report in zip((parent, candidate), (a, b), reports, strict=True):
        if uses_official_swe(manifest["experiment"], output):
            selected, issues = official_trials(output, manifest["experiment"], report.get("trials", []))
            report["trials"] = selected
            problems.extend(issues)
    contracts = []
    for manifest in (a, b):
        agents = []
        for agent in manifest["experiment"]["agents"]:
            agents.append(
                {
                    **{
                        k: v for k, v in agent.items() if k not in {"version", "kwargs"}
                    },
                    "kwargs": {
                        k: v
                        for k, v in agent.get("kwargs", {}).items()
                        if k not in {"binary", "bundle", "provenance"}
                    },
                }
            )
        contracts.append(agents)
    if len(contracts[0]) != 1 or contracts[0] != contracts[1]:
        problems.append("contract mismatch: agents")
    for field in (
        "model",
        "benchmark",
        "attempts",
        "timeout_seconds",
        "setup_timeout_seconds",
        "cpus",
        "memory_mb",
        "concurrency",
        "guardian",
    ):
        left, right = a["experiment"].get(field), b["experiment"].get(field)
        if field == "benchmark":
            left, right = (
                {k: v for k, v in left.items() if k != "tasks"},
                {k: v for k, v in right.items() if k != "tasks"},
            )
        if left != right:
            problems.append("contract mismatch: " + field)
    task_hashes = [{k: v.get("sha256") for k, v in m["tasks"].items()} for m in (a, b)]
    if task_hashes[0] != task_hashes[1] or any(not v for v in task_hashes[0].values()):
        problems.append("task identity differs or is unknown")
    metric = a["experiment"]["benchmark"]["primary_metric"]
    expected = len(a["tasks"]) * a["experiment"]["attempts"]
    if not expected:
        problems.append("empty task set")
    values, errors = [], []
    for report in reports:
        trials = report.get("trials", [])
        if report.get("report_errors") or len(trials) != expected:
            problems.append("incomplete or malformed report")
        for task in a["tasks"]:
            if (
                sum(t.get("task") == task for t in trials)
                != a["experiment"]["attempts"]
            ):
                problems.append("missing or duplicate task attempts")
        if any(t.get("guardian", {}).get("assisted") for t in trials):
            problems.append("externally assisted attempts")
        scores = [
            t.get("metrics", {}).get(metric) for t in trials if not t.get("exception")
        ]
        if any(type(s) not in (int, float) or not math.isfinite(s) for s in scores):
            problems.append("unknown or invalid primary metric")
        errors.append(sum(bool(t.get("exception")) for t in trials))
        values.append(scores)
    outcome = {
        "assessment": "inconclusive",
        "metric": metric,
        "errors": {"parent": errors[0], "candidate": errors[1]},
        "issues": sorted(set(problems)),
        "note": "Measured on this configured set only; no statistical/general capability claim.",
    }
    if problems:
        return outcome
    outcome["reliability_change"] = (
        "improved"
        if errors[1] < errors[0]
        else "regressed"
        if errors[1] > errors[0]
        else "equal"
    )
    if errors[0] or errors[1]:
        outcome["issues"] = [
            "execution errors prevent a complete score comparison; see reliability_change"
        ]
        return outcome
    means = [sum(scores) / expected for scores in values]
    outcome.update(
        assessment="improved"
        if means[1] > means[0]
        else "regressed"
        if means[1] < means[0]
        else "equal",
        scores={
            "parent": means[0],
            "candidate": means[1],
            "delta": means[1] - means[0],
        },
    )
    return outcome


async def evaluate(store, record, allow_live):
    from . import regression

    if regression.enabled(record):
        return await regression.evaluate(store, record, allow_live)
    return await replay_tasks(store, record, allow_live)


async def replay_tasks(store, record, allow_live):
    profile, exp, task_hashes = configuration(store, record)
    if exp.model.mode == "live" and not allow_live:
        raise ValueError(
            "Live reevaluation requires server permission and explicit unbounded spending acknowledgement"
        )
    if task_hashes:
        from .evidence import verify_case

        verify_case(store, store.get("cases", record["case"]))
        write(profile.evaluation, exp.model_dump(mode="json"))
        exp = load(profile.evaluation, validate_agent_artifacts=False)
    agent_id = store.get("cases", record["case"])["agent"]
    agents = [a for a in exp.agents if a.id == agent_id]
    if len(agents) != 1:
        raise ValueError("Evaluation template must contain the case Agent")
    exp.agents = agents
    required = set(agents[0].kwargs) & {"binary", "bundle", "provenance"}
    if not profile.integration_recipe and not required <= set(profile.artifacts):
        raise ValueError(
            "Build must replace all existing source-dependent Agent artifacts"
        )
    if not {"binary", "bundle"}.intersection(profile.artifacts):
        raise ValueError(
            "Paired evaluation requires a candidate-built binary or bundle"
        )
    # Both versions use the same independent deadline protection, never assist.
    if exp.guardian and exp.guardian.mode == "assist":
        raise ValueError("RSI paired evaluation requires unassisted evaluation")
    exp.guardian = exp.guardian or GuardianConfig()
    root = store.path("candidates", record["id"])
    await assert_clean(root / "workspace", record["commit"])
    evaluation = {
        "id": uuid4().hex,
        "kind": "task_replay",
        "started_at": now(),
        "assessment": "running",
        "parent_commit": record["parent_commit"],
        "candidate_commit": record["commit"],
    }
    if task_hashes:
        record.setdefault(
            "paired_build",
            {
                "build": profile.build.model_dump(mode="json"),
                "artifacts": profile.artifacts,
            },
        )
    record["evaluations"].append(evaluation)
    store.save("candidates", record)
    destination = root / "evaluations" / evaluation["id"]
    destination.mkdir(parents=True)
    targeted, built, paired = {}, [], {}
    holdout_result = None
    previous_env = {}
    try:
        if exp.model.mode == "live":
            from ..model_pool import ModelPool
            from .executors import credential

            env = ModelPool(store.project).environment(exp.model.model_dump())
            if exp.model.api_key_env == "DEEPSEEK_API_KEY" and not os.environ.get(
                "DEEPSEEK_API_KEY"
            ):
                env["DEEPSEEK_API_KEY"] = credential(store.project)
            previous_env = {name: os.environ.get(name) for name in env}
            os.environ.update(env)
            if not os.environ.get(exp.model.api_key_env):
                raise ValueError("原测试使用的模型凭据不可用，请恢复对应凭据后重试")
        frozen_tasks = []
        for task in exp.benchmark.tasks:
            validate_tree(task)
            frozen = destination / "inputs" / "tasks" / task.name
            shutil.copytree(task, frozen)
            if (
                task_hashes
                and tree_manifest(frozen)["sha256"] != task_hashes[task.name]
            ):
                raise ValueError(
                    f"题目 {task.name} 与原测试的冻结哈希不一致，拒绝使用已变化的输入"
                )
            frozen_tasks.append(frozen)
        exp.benchmark.tasks = frozen_tasks
        write(destination / "experiment.json", exp.model_dump(mode="json"))
        write(destination / "profile.json", profile.model_dump(mode="json"))
        for side, commit in (
            ("parent", record["parent_commit"]),
            ("candidate", record["commit"]),
        ):
            workspace, output = (
                destination / (side + "-workspace"),
                destination / (side + "-build"),
            )
            # Candidate clone includes both parent and child commits.
            await clone(root / "workspace", workspace, commit)
            output.mkdir()
            await checked(
                profile.build,
                workspace,
                destination / (side + "-build.json"),
                {
                    "workspace": workspace,
                    "output": output,
                    "evidence": store.path("cases", record["case"]) / "evidence",
                },
                lane=None if profile.integration_recipe else "build",
            )
            await assert_clean(workspace, commit)
            built.append(side)
            if profile.integration_recipe:
                from .validation import targeted_side

                targeted[side] = await targeted_side(profile, workspace, destination / (side + "-targeted"))
                await assert_clean(workspace, commit)
            config = exp.model_copy(deep=True)
            config.agents[0].version = commit
            if profile.integration_recipe:
                from ..config import Agent

                receipt = read(output / "release/provenance.json")
                config.agents[0] = Agent.model_validate({"id": agent_id,
                    "implementation": "agent_harness_eval.agents.recipe:Recipe", "version": receipt["release_id"],
                    "capabilities": receipt["recipe"]["capabilities"], "kwargs": {}})
            artifacts = {}
            for key, relative in profile.artifacts.items():
                artifact = output / relative
                if (
                    artifact.is_symlink()
                    or not artifact.resolve().is_relative_to(output.resolve())
                    or not artifact.is_file()
                ):
                    raise ValueError("Build output missing or escapes output directory")
                config.agents[0].kwargs[key] = str(artifact)
                artifacts[key] = {"sha256": digest(artifact), "commit": commit}
            write(destination / (side + "-artifacts.json"), artifacts)
            paired[side] = config.model_copy(deep=True)
            await run_experiment(config, destination / side, manage_signals=False)
        evaluation.update(compare(destination / "parent", destination / "candidate"))
        if profile.integration_recipe and profile.evaluation_holdout:
            holdout = load(profile.evaluation_holdout, validate_agent_artifacts=False)
            if holdout.model != exp.model or (holdout.guardian and holdout.guardian.mode == "assist"):
                raise ValueError("Holdout must use the same model and autonomous execution")
            if {task.name for task in holdout.benchmark.tasks} & {task.name for task in exp.benchmark.tasks}:
                raise ValueError("Holdout task names overlap replay inputs")
            if {tree_manifest(task)["sha256"] for task in holdout.benchmark.tasks} & {tree_manifest(task)["sha256"] for task in exp.benchmark.tasks}:
                raise ValueError("Holdout task contents overlap replay inputs")
            from ..freezing import freeze_tree

            tasks = []
            for task in holdout.benchmark.tasks:
                target = destination / "holdout-inputs" / task.name
                freeze_tree(task, target, destination / "holdout-runtime")
                tasks.append(target)
            for side in ("parent", "candidate"):
                config = holdout.model_copy(deep=True)
                config.benchmark.tasks = tasks
                config.agents = paired[side].agents
                await run_experiment(config, destination / (side + "-holdout"), manage_signals=False)
            holdout_result = compare(destination / "parent-holdout", destination / "candidate-holdout")
            write(destination / "holdout-comparison.json", holdout_result)
    except BaseException as error:
        evaluation.update(assessment="failed", error=str(error) or type(error).__name__)
        raise
    finally:
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        evaluation["finished_at"] = now()
        write(destination / "comparison.json", evaluation)
        if profile.integration_recipe:
            from .recipe_build import verify_recipe
            from .validation import report, targeted_passed
            from ..policies.model import freeze

            inputs = {"parent_commit": record["parent_commit"], "candidate_commit": record["commit"]}
            levels = {
                    "build": {**inputs, "status": "passed" if len(built) == 2 else "failed", "evidence": [str(destination / (side + "-build.json")) for side in built]},
                    "targeted": {**inputs, "status": "passed" if targeted_passed(targeted) else "inconclusive", "evidence": [str(destination / (side + "-targeted/results.json")) for side in targeted]},
                    "benchmark-replay": {**inputs, "status": "passed" if evaluation["assessment"] in {"improved", "equal", "regressed"} else "failed", "assessment": evaluation["assessment"], "issues": evaluation.get("issues", []), "evidence": [str(destination / "comparison.json")]}}
            if holdout_result:
                levels["holdout"] = {**inputs, "status": "passed" if holdout_result["assessment"] in {"improved", "equal"} else "failed",
                    "assessment": holdout_result["assessment"], "issues": holdout_result.get("issues", []), "evidence": [str(destination / "holdout-comparison.json")]}
            record["validation"] = report(**inputs, recipe_digest=verify_recipe(profile.integration_recipe).digest,
                model_policy_digest=freeze(exp.model).digest, required=profile.required_validation, levels=levels)
            write(destination / "validation.json", record["validation"])
        store.save("candidates", record)
