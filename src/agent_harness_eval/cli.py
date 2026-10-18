import argparse
from pathlib import Path

from .config import load
from .provenance import digest, source_manifest, write_json
from .report import report
from .run import execute


def main():
    p = argparse.ArgumentParser(
        description="Agent harness evaluation workbench (Harbor)"
    )
    sub = p.add_subparsers(dest="command", required=True)
    from .rsi.cli import register

    register(sub)
    q = sub.add_parser("serve", help="启动本机评测控制台")
    q.add_argument("--root", type=Path, default=Path.cwd())
    q.add_argument("--port", type=int, default=8765)
    q.add_argument(
        "--host", default="127.0.0.1", help="监听的具体 IP 地址（例如 Tailscale IP）"
    )
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
            q.add_argument(
                "--resume",
                action="store_true",
                help="恢复同一批次实验；不重跑已落盘尝试",
            )
            q.add_argument(
                "--workbench-repair", type=Path,
                help="显式记录并校验基础设施修复前后代码清单；仅与 --resume 一起使用",
            )
    q = sub.add_parser("report")
    q.add_argument("run", type=Path)
    for name in ("manifest-index", "manifest-verify"):
        q = sub.add_parser(name)
        q.add_argument("run", type=Path)
    q = sub.add_parser("snapshot-bkcrab")
    q.add_argument("source", type=Path)
    q.add_argument("binary", type=Path)
    q.add_argument("output", type=Path)
    q = sub.add_parser("bundle-dsh")
    q.add_argument("runtime", type=Path)
    q.add_argument("output", type=Path)
    q = sub.add_parser("terminal-bench-prepare", help="下载固定版本官方 Terminal-Bench，生成工作台实验配置（不启动模型）")
    q.add_argument("--dataset", required=True, help="例如 terminal-bench@2.0 或 terminal-bench/terminal-bench@4.0.0")
    q.add_argument("--destination", type=Path, required=True)
    q.add_argument("--template", type=Path, required=True, help="复用已有实验的 Agent 与模型配置")
    q.add_argument("--config-output", type=Path, help="实验配置路径；保存到 experiments/ 后控制台可发现")
    selection = q.add_mutually_exclusive_group()
    selection.add_argument("--task", action="append", dest="tasks", help="指定任务名，可重复传不同任务")
    selection.add_argument("--count", type=int, help="按 seed 确定性抽样；不传则使用全部任务")
    q.add_argument("--seed", type=int, default=20261017)
    q.add_argument("--cpu-only", action="store_true", help="显式排除 GPU/TPU 任务，并记录为 CPU 子集")
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
    if args.command.startswith("rsi-"):
        from .rsi.cli import execute as execute_rsi

        execute_rsi(args)
    elif args.command == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(
            create_app(args.root, args.allow_live, args.recover, bind_host=args.host),
            host=args.host,
            port=args.port,
        )
    elif args.command == "validate":
        e = load(args.config)
        print(
            f"Valid: {len(e.agents)} agents x {len(e.benchmark.tasks)} tasks x {e.attempts} attempts; mode={e.model.mode}"
        )
    elif args.command == "run":
        execute(load(args.config), args.output, resume=args.resume, workbench_repair=args.workbench_repair)
    elif args.command == "report":
        report(args.run)
    elif args.command == "manifest-index":
        from .manifests import index_legacy_manifest

        print(index_legacy_manifest(args.run))
    elif args.command == "manifest-verify":
        from .manifests import verify_manifest

        verify_manifest(args.run)
        print("Manifest audit passed")
    elif args.command == "snapshot-bkcrab":
        write_json(
            args.output,
            {
                **source_manifest(
                    args.source.resolve(), args.output.with_suffix(".source")
                ),
                "binary_sha256": digest(args.binary),
                "profile": "isolated-coding",
                "web_fetch_review": (args.source / "internal/agent/tools/web_fetch_review.go").is_file()
                and b"EVAL_FETCH_REVIEW_URL" in args.binary.read_bytes(),
            },
        )
    elif args.command == "bundle-dsh":
        from .dsh_bundle import build_bundle

        build_bundle(args.runtime.resolve(), args.output.resolve())
    elif args.command == "terminal-bench-prepare":
        import asyncio
        import json

        from .benchmarks.terminal import prepare

        result = asyncio.run(prepare(
            args.dataset, args.destination, load(args.template),
            config_output=args.config_output, names=args.tasks, count=args.count,
            seed=args.seed, cpu_only=args.cpu_only,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
