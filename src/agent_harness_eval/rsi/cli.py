import asyncio
import json
from pathlib import Path

def register(sub):
    q = sub.add_parser(
        "rsi-case", help="Freeze an evaluation diagnosis case (no model execution)"
    )
    q.add_argument("run", type=Path)
    q.add_argument("--agent", required=True)
    q.add_argument("--trials", nargs="+")
    q.add_argument("--root", type=Path, default=Path.cwd())
    q = sub.add_parser(
        "rsi-candidate", help="Create an isolated improvement candidate from a case"
    )
    q.add_argument("case")
    q.add_argument("--profile", required=True)
    q.add_argument("--parent")
    q.add_argument("--root", type=Path, default=Path.cwd())
    q = sub.add_parser(
        "rsi-action", help="Queue improve/evaluate/publish; optionally execute now"
    )
    q.add_argument("candidate")
    q.add_argument("kind", choices=["improve", "evaluate", "publish"])
    q.add_argument("--execute", action="store_true")
    q.add_argument("--allow-live", action="store_true")
    q.add_argument("--confirm-unbounded", action="store_true")
    q.add_argument("--root", type=Path, default=Path.cwd())
    q = sub.add_parser("rsi-work", help="Execute a queued RSI action")
    q.add_argument("action")
    q.add_argument("--allow-live", action="store_true")
    q.add_argument("--root", type=Path, default=Path.cwd())


def execute(args):
    from .evidence import create_case
    from .runner import create_candidate, perform
    from .store import Store

    store = Store(args.root)
    if args.command == "rsi-case":
        result = create_case(store, args.run, args.agent, args.trials)
    elif args.command == "rsi-candidate":
        result = asyncio.run(
            create_candidate(store, args.case, args.profile, args.parent)
        )
    elif args.command == "rsi-action":
        result = store.enqueue(args.candidate, args.kind, args.confirm_unbounded)
        if args.execute:
            asyncio.run(perform(store, result["id"], args.allow_live))
            result = store.action(result["id"])
    else:
        asyncio.run(perform(store, args.action, args.allow_live))
        result = store.action(args.action)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") in {"failed", "interrupted"}:
        raise SystemExit(1)
