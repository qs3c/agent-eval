import argparse
from pathlib import Path

from .config import load
from .provenance import digest, source_manifest, write_json
from .report import report
from .run import execute


def main():
    p = argparse.ArgumentParser(description="Agent evaluation workbench (Harbor)")
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("serve", help="启动本机评测控制台")
    q.add_argument("--root", type=Path, default=Path.cwd())
    q.add_argument("--port", type=int, default=8765)
    q.add_argument("--allow-live", action="store_true")
    q.add_argument(
        "--recover",
        action="store_true",
        help="确认已检查并清理中断运行的进程和 Docker 容器，恢复队列",
    )
    for name in ("validate", "run"):
        q = sub.add_parser(name)
        q.add_argument("config", type=Path)
        if name == "run":
            q.add_argument("--output", type=Path, required=True)
    q = sub.add_parser("report")
    q.add_argument("run", type=Path)
    q = sub.add_parser("snapshot-bkcrab")
    q.add_argument("source", type=Path)
    q.add_argument("binary", type=Path)
    q.add_argument("output", type=Path)
    q = sub.add_parser("bundle-dsh")
    q.add_argument("runtime", type=Path)
    q.add_argument("output", type=Path)
    q = sub.add_parser("swe-export")
    q.add_argument("run", type=Path)
    q.add_argument("output", type=Path)
    q = sub.add_parser("swe-instrument")
    q.add_argument("task", type=Path)
    q = sub.add_parser("swe-regrade")
    q.add_argument("predictions", type=Path)
    q.add_argument("--dataset", default="princeton-nlp/SWE-bench_Verified")
    q.add_argument("--run-id", required=True)
    q.add_argument("--execute", action="store_true")
    args = p.parse_args()
    if args.command == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(
            create_app(args.root, args.allow_live, args.recover),
            host="127.0.0.1",
            port=args.port,
        )
    elif args.command == "validate":
        e = load(args.config)
        print(
            f"Valid: {len(e.agents)} agents x {len(e.benchmark.tasks)} tasks x {e.attempts} attempts; mode={e.model.mode}"
        )
    elif args.command == "run":
        execute(load(args.config), args.output)
    elif args.command == "report":
        report(args.run)
    elif args.command == "snapshot-bkcrab":
        write_json(
            args.output,
            {
                **source_manifest(args.source.resolve()),
                "binary_sha256": digest(args.binary),
                "profile": "isolated-coding",
            },
        )
    elif args.command == "bundle-dsh":
        from .dsh_bundle import build_bundle

        build_bundle(args.runtime.resolve(), args.output.resolve())
    elif args.command == "swe-export":
        from .benchmarks.swe import export

        export(args.run, args.output)
    elif args.command == "swe-instrument":
        from .benchmarks.swe import instrument

        instrument(args.task)
    elif args.command == "swe-regrade":
        from .benchmarks.swe import regrade

        regrade(args.predictions, args.dataset, args.run_id, args.execute)


if __name__ == "__main__":
    main()
