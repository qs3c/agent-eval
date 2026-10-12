from fastapi import APIRouter
from pydantic import Field

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
    objective: str = Field(min_length=1, max_length=16000)


class CandidateRequest(Strict):
    case_id: str
    profile: str
    parent: str | None = None


class ActionRequest(Strict):
    kind: str
    confirm_unbounded: bool = False


def public_candidate(record):
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
            "pr_url",
            "last_error",
            "source_checkout_dirty_ignored",
            "measured_sources",
            "matches_measured_clean_commit",
        )
    }
    result["can_evaluate"] = bool(record["profile"].get("evaluation"))
    result["can_publish"] = bool(record["profile"].get("github_repository"))
    return result


def router(console):
    store = Store(console.root)
    routes = APIRouter(prefix="/api/rsi")

    @routes.get("")
    def overview():
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
                {k: v for k, v in c.items() if k not in {"evidence", "run"}}
                for c in store.list("cases")
            ],
            "candidates": [public_candidate(c) for c in store.list("candidates")],
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
            data.objective,
        )
        record["source_run_id"] = data.run_id
        store.save("cases", record)
        return record

    @routes.get("/cases/{identity}")
    def case(identity: str):
        return store.get("cases", identity)

    @routes.post("/candidates", status_code=201)
    async def new_candidate(data: CandidateRequest):
        return public_candidate(
            await create_candidate(store, data.case_id, data.profile, data.parent)
        )

    @routes.get("/candidates/{identity}")
    def candidate(identity: str):
        record = public_candidate(store.get("candidates", identity))
        root = store.path("candidates", identity)
        for key, name in (("patch", "changes.patch"), ("pr_body", "PR.md")):
            path = root / name
            if path.is_file() and not path.is_symlink():
                with path.open(encoding="utf-8") as stream:
                    record[key] = redact(stream.read(65536))
        return record

    @routes.post("/candidates/{identity}/actions", status_code=201)
    def action(identity: str, data: ActionRequest):
        if data.kind == "evaluate":
            from ..config import load
            from .models import Profile

            profile = Profile.model_validate(
                store.get("candidates", identity)["profile"]
            )
            if (
                profile.evaluation
                and load(profile.evaluation, validate_agent_artifacts=False).model.mode
                == "live"
                and (not console.allow_live or not data.confirm_unbounded)
            ):
                raise ValueError(
                    "Live reevaluation needs enabled live execution and spending acknowledgement"
                )
        return store.enqueue(identity, data.kind, data.confirm_unbounded)

    return routes
