import argparse
from pathlib import Path

def main():
    p = argparse.ArgumentParser(
        description="Agent harness evaluation workbench (Harbor)"
    )
    sub = p.add_subparsers(dest="command", required=True)
    from .rsi.cli import register

    register(sub)
    from .doctor import register as register_doctor

    register_doctor(sub)
    from .integrations.commands import register as register_integrations

    register_integrations(sub)
    from .benchmarks.commands import register as register_benchmarks

    register_benchmarks(sub)
    q = sub.add_parser("resources", help="查看或配置本用户所有工作区共享的主机预算")
    q.add_argument("action", choices=["status", "configure", "release"])
    q.add_argument("--config", type=Path, help="预算 JSON 文件；只能在全部资源预留释放后修改")
    q.add_argument("--id")
    q.add_argument("--owner")
    q.add_argument("--generation", type=int)
    q.add_argument("--acknowledge-cleanup", action="store_true")
    q = sub.add_parser("grade-work", help=argparse.SUPPRESS)
    q.add_argument("id")
    q = sub.add_parser("manager", help="独立本机执行进程；关闭 Web 不停止实验")
    q.add_argument("action", choices=["start", "run", "status", "stop"])
    q.add_argument("--root", type=Path, default=Path.cwd())
    q.add_argument("--allow-live", action="store_true")
    q.add_argument("--cancel", action="store_true", help="停止时取消队列和在途作业；默认排空当前作业")
    q = sub.add_parser("operation", help="查询或控制统一作业")
    q.add_argument("action", choices=["list", "query", "pause", "resume", "cancel", "acknowledge"])
    q.add_argument("id", nargs="?")
    q.add_argument("--root", type=Path, default=Path.cwd())
    q.add_argument("--acknowledge-cleanup", action="store_true", help="确认已检查中断执行的进程、容器和外部请求；不重跑")
    q = sub.add_parser("workspace-import", help="预览旧格式导入；--apply 才写新索引，不调度旧作业")
    q.add_argument("--root", type=Path, default=Path.cwd())
    q.add_argument("--regrading-root", type=Path)
    q.add_argument("--apply", action="store_true")
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
    q = sub.add_parser("snapshot-agent")
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
    q.add_argument("--root", type=Path, default=Path.cwd())
    q = sub.add_parser("swe-grade-work", help="内部官方评分 Worker")
    q.add_argument("job")
    q.add_argument("--root", type=Path, required=True)
    args = p.parse_args()
    if args.command == "benchmark":
        import json
        from .benchmarks.commands import dispatch

        print(json.dumps(dispatch(args), ensure_ascii=False, indent=2))
        return
    if args.command in {"integration", "integration-work", "integration-assist-work", "source", "source-work", "cycle", "cycle-work"}:
        import json
        from .integrations.commands import dispatch

        result = dispatch(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get("status") in {"failed", "interrupted", "cancelled"}:
            raise SystemExit(1)
        return
    if args.command == "resources":
        import json
        from .operations.resources import Resources

        ledger = Resources()
        ledger.reconcile()
        if args.action == "configure":
            if not args.config:
                p.error("resources configure requires --config")
            result = ledger.configure(json.loads(args.config.read_text()))
        elif args.action == "release":
            from .operations.recovery import start_ticks

            if not all((args.id, args.owner, args.generation, args.acknowledge_cleanup)):
                p.error("resources release requires --id --owner --generation --acknowledge-cleanup")
            row = next((row for row in ledger.snapshot()["reservations"] if row["id"] == args.id), None)
            if row and start_ticks(row["pid"]) is not None and start_ticks(row["pid"]) == row["start_ticks"]:
                p.error("The resource owner is still alive")
            ledger.release(args.id, owner=args.owner, generation=args.generation)
            result = ledger.snapshot()
        else:
            result = ledger.snapshot()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "grade-work":
        import signal
        from .operations.worker import is_managed_worker
        from .regrading import execute_queued

        if not is_managed_worker():
            p.error("grade-work requires Manager ownership")
        def interrupted(*_):
            raise KeyboardInterrupt("Grading cancelled")
        signal.signal(signal.SIGTERM, interrupted)
        execute_queued(args.id)
        return
    if args.command == "manager":
        import json
        from .operations.manager import Manager, ensure, status, stop

        if args.action == "run":
            Manager(args.root, allow_live=args.allow_live).run()
        else:
            result = (ensure(args.root, allow_live=args.allow_live) if args.action == "start"
                      else stop(args.root, cancel=args.cancel) if args.action == "stop" else status(args.root))
            print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "operation":
        import json
        from .operations.commands import Commands

        commands = Commands(args.root)
        if args.action != "list" and not args.id:
            p.error("operation requires an id")
        if args.action == "acknowledge":
            if not args.acknowledge_cleanup:
                p.error("acknowledge requires --acknowledge-cleanup after external resource inspection")
            result = commands.acknowledge_interruption(args.id)
        else:
            result = (commands.list() if args.action == "list" else commands.query(args.id)
                      if args.action == "query" else commands.control(args.id, args.action))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "doctor":
        from .doctor import execute as execute_doctor

        execute_doctor(args)
        return
    if args.command == "workspace-import":
        import json
        from .workspace.legacy import import_legacy

        print(json.dumps(import_legacy(args.root, regrading_root=args.regrading_root,
                                      dry_run=not args.apply), ensure_ascii=False, indent=2))
        return

    from .config import load
    from .provenance import digest, source_manifest, write_json

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
        from .operations.worker import is_managed_worker

        if is_managed_worker():
            from .run import execute

            execute(load(args.config), args.output, resume=args.resume, workbench_repair=args.workbench_repair)
        else:
            from .operations.commands import Commands
            from .operations.manager import ensure
            from .operations.submission import submit_experiment, wait

            exp = load(args.config)
            root = Path.cwd()
            ensure(root, allow_live=exp.model.mode == "live" or bool(exp.guardian and exp.guardian.model))
            job = submit_experiment(root, exp, args.output, resume=args.resume, workbench_repair=args.workbench_repair)
            print("Operation: " + job["id"], flush=True)
            result = wait(Commands(root), job["id"])
            if result["status"] != "completed":
                raise SystemExit(result["error"] or result["status"])
    elif args.command == "report":
        from .report import report

        report(args.run)
    elif args.command == "manifest-index":
        from .manifests import index_legacy_manifest

        print(index_legacy_manifest(args.run))
    elif args.command == "manifest-verify":
        from .manifests import verify_manifest

        verify_manifest(args.run)
        print("Manifest audit passed")
    elif args.command == "snapshot-agent":
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
    elif args.command == "swe-grade-work":
        from .operations.official import perform

        perform(args.root, args.job)
    elif args.command == "swe-regrade":
        from .benchmarks.swe import regrade

        if not args.execute:
            regrade(args.predictions, args.dataset, args.run_id, False)
        else:
            from .operations.official import submit
            from .operations.manager import ensure, status
            from .operations.commands import Commands
            from .operations.submission import wait

            current = status(args.root)
            ensure(args.root, allow_live=current.get("allow_live", False))
            job = submit(args.root, args.predictions, args.dataset, args.run_id, Path.cwd())
            print("Operation: " + job["id"], flush=True)
            result = wait(Commands(args.root), job["id"])
            if result["status"] != "completed":
                raise SystemExit(result["error"] or result["status"])


if __name__ == "__main__":
    main()
