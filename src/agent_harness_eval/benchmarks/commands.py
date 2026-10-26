"""Benchmark inspection and deterministic subset materialization."""

from pathlib import Path


def register(sub):
    parser = sub.add_parser("benchmark", help="检查 benchmark 契约或冻结可复现子集")
    parser.add_argument("action", choices=["list", "inspect", "select"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--count", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--exclude-run", type=Path, action="append", default=[])


def dispatch(args):
    if args.action == "list":
        return [{"id": "controls", "execution": "Harbor", "scorer": "task verifier"},
                {"id": "harbor-tasks", "execution": "Harbor", "scorer": "task verifier"},
                {"id": "swe", "execution": "Harbor", "scorer": "versioned official regrade"}]
    from ..config import load
    from ..provenance import write_json
    from .registry import resolve
    from .selection import select, historical_tasks

    if not args.config:
        raise ValueError("--config is required")
    experiment = load(args.config)
    plugin = resolve(experiment.benchmark, batch=experiment.batch)
    revision = plugin.resolve(experiment.benchmark)
    if args.action == "inspect":
        return {"plugin": plugin.id, "revision": revision.model_dump(mode="json")}
    if not args.output or args.count is None:
        raise ValueError("--output and --count are required")
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Selection output already exists")
    selection = select(experiment.benchmark.tasks, count=args.count, seed=args.seed,
        excluded=historical_tasks(args.exclude_run, benchmark_id=experiment.benchmark.id), dataset=revision.dataset)
    experiment.benchmark.tasks = plugin.materialize(experiment.benchmark.tasks, selection, output / "tasks")
    write_json(output / "experiment.json", experiment.model_dump(mode="json"))
    return {"config": str(output / "experiment.json"), "selection": selection}
