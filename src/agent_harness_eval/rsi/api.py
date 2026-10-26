from fastapi import APIRouter

from ..console import redact
from .evidence import create_case
from .executors import ExecutorSettings, builtin_profiles, catalog, save_settings
from .models import Strict, load_profile
from .runner import create_candidate
from .store import Store


class CaseRequest(Strict):
    run_id: str
    agent: str
    trials: list[str] | None = None


class CandidateRequest(Strict):
    case_id: str
    profile: str
    parent: str | None = None


class ActionRequest(Strict):
    kind: str
    confirm_unbounded: bool = False


class CycleRequest(Strict):
    source_id: str
    evaluation: str
    candidate: str | None = None
    max_rounds: int = 1
    max_attempts: int = 20
    max_seconds: int = 86400
    confirm_paid: bool = False


def public_candidate(record, store=None):
    # Do not expose administrator commands/env or raw adapter output.
    result = {
        k: record.get(k)
        for k in (
            "id",
            "case",
            "profile_id",
            "parent_candidate",
            "parent_commit",
            "commit",
            "phase",
            "created_at",
            "updated_at",
            "diagnosis",
            "checks",
            "evaluations",
            "validation",
            "validation_stale",
            "validation_stale_reason",
            "upstream_observation",
            "pr_url",
            "pr_state",
            "pr_sync_error",
            "pr_ready_reason",
            "pr_synced_at",
            "last_error",
            "source_checkout_dirty_ignored",
            "measured_sources",
            "matches_measured_clean_commit",
        )
    }
    result["can_evaluate"] = bool(record["profile"].get("evaluation"))
    if store is not None:
        from . import regression
        from .native_evaluation import configuration

        try:
            if regression.enabled(record):
                regression.configuration(store, record)
                result.update(
                    can_evaluate=True,
                    evaluation_requires_live=True,
                    evaluation_kind="targeted_regression",
                    evaluation_planner="dsh",
                )
            else:
                _, exp, hashes = configuration(store, record)
                result.update(
                    can_evaluate=True,
                    evaluation_requires_live=exp.model.mode == "live",
                    evaluation_tasks=[p.name for p in exp.benchmark.tasks],
                    evaluation_attempts=exp.attempts,
                    evaluation_from_case=bool(hashes),
                )
        except (ValueError, OSError, KeyError) as error:
            result.update(can_evaluate=False, evaluation_blocked_reason=str(error))
    result["can_publish"] = bool(record["profile"].get("github_repository"))
    return result


def diagnosis_topics(candidates):
    topics = {}
    for candidate in candidates:  # Store.list is newest first.
        topic = (candidate.get("diagnosis") or {}).get("topic", "")
        if topic and any("\u4e00" <= char <= "\u9fff" for char in topic):
            topics.setdefault(
                candidate["case"],
                {"diagnosis_topic": topic, "diagnosis_candidate_id": candidate["id"]},
            )
    return topics


def router(console):
    store = Store(console.root)
    routes = APIRouter(prefix="/api/rsi")

    @routes.get("/cycles")
    def cycles():
        from .cycle import list_cycles

        return {"cycles": list_cycles(console.root)}

    @routes.post("/cycles", status_code=202)
    def create_cycle(value: CycleRequest):
        from .cycle import create, queue
        from ..config import load
        from pathlib import Path

        if load(Path(value.evaluation), validate_agent_artifacts=False).model.mode == "live" and not console.allow_live:
            raise ValueError("真实模型闭环需要 --allow-live")
        record = create(console.root, **value.model_dump())
        return {"cycle": record, "job": queue(console.root, record["id"])}

    @routes.post("/cycles/{identity}/refresh", status_code=202)
    def refresh_cycle(identity: str):
        from .cycle import queue

        return queue(console.root, identity)

    @routes.post("/cycles/{identity}/stop")
    def stop_cycle(identity: str):
        from .cycle import stop

        return stop(console.root, identity)

    @routes.get("")
    def overview():
        candidates = store.list("candidates")
        topics = diagnosis_topics(candidates)
        profiles = []
        for path in sorted((console.root / "rsi-profiles").glob("*.json")):
            try:
                profile = load_profile(console.root, path.stem)
                profiles.append(
                    {
                        "id": path.stem,
                        "version": profile.adapter.version,
                        "evaluation": bool(profile.evaluation),
                        "publication": bool(profile.github_repository),
                    }
                )
            except (ValueError, OSError):
                continue
        return {
            "cases": [
                {
                    **{k: v for k, v in c.items() if k not in {"evidence", "run"}},
                    **topics.get(c["id"], {}),
                }
                for c in store.list("cases")
            ],
            "candidates": [public_candidate(c, store) for c in candidates],
            "actions": store.actions(),
            "profiles": builtin_profiles(console.root) + profiles,
            "executor_settings": catalog(console.root),
            "allow_live": console.allow_live,
        }

    @routes.put("/executor-settings")
    def configure_executor(data: ExecutorSettings):
        return save_settings(console.root, data)

    @routes.post("/cases", status_code=201)
    def new_case(data: CaseRequest):
        run = console.detail(data.run_id)
        if run["status"] in {"queued", "running"}:
            raise ValueError(
                "Wait for the experiment to finish before freezing an RSI case"
            )
        record = create_case(
            store,
            console.row(data.run_id)["path"],
            data.agent,
            data.trials,
        )
        record["source_run_id"] = data.run_id
        store.save("cases", record)
        return record

    @routes.get("/cases/{identity}")
    def case(identity: str):
        return {
            **store.get("cases", identity),
            **diagnosis_topics(store.list("candidates")).get(identity, {}),
        }

    @routes.post("/candidates", status_code=201)
    async def new_candidate(data: CandidateRequest):
        return public_candidate(
            await create_candidate(store, data.case_id, data.profile, data.parent),
            store,
        )

    @routes.get("/candidates/{identity}")
    def candidate(identity: str):
        record = public_candidate(store.get("candidates", identity), store)
        root = store.path("candidates", identity)
        for key, name in (("patch", "changes.patch"), ("pr_body", "PR.md")):
            path = root / name
            if path.is_file() and not path.is_symlink():
                with path.open(encoding="utf-8") as stream:
                    record[key] = console.models.redact(redact(stream.read(65536)))
        return record

    @routes.post("/candidates/{identity}/actions", status_code=201)
    def action(identity: str, data: ActionRequest):
        if data.kind == "evaluate":
            from . import regression
            from .native_evaluation import configuration

            record = store.get("candidates", identity)
            if regression.enabled(record):
                regression.configuration(store, record)
                live = True
            else:
                _, exp, _ = configuration(store, record)
                live = exp.model.mode == "live"
            if live and (not console.allow_live or not data.confirm_unbounded):
                raise ValueError(
                    "复评调用模型需要服务开启 --allow-live，并勾选本次复评费用确认"
                )
        return store.enqueue(identity, data.kind, data.confirm_unbounded)

    return routes
