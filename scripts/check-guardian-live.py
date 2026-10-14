"""Explicitly authorized online preflight; prints no provider credentials."""

import argparse
import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

from agent_harness_eval.config import load
from agent_harness_eval.guardian.docker import DockerTarget
from agent_harness_eval.guardian.driver import run_driver
from agent_harness_eval.guardian.evidence import capture, read, write
from agent_harness_eval.model_pool import ModelPool
from agent_harness_eval.resources import live_model as relay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("--allow-live is required; this invokes the configured paid APIs")
    args.output.mkdir(parents=True, exist_ok=False)
    exp = load(args.config)
    pool = ModelPool(args.root)
    os.environ.update(pool.environment(exp.model.model_dump(), exp.guardian.model))
    relay.LOGS = args.output / "search"
    relay.LOGS.mkdir()
    server = relay.ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
    server.model_config = exp.model.model_dump()
    server.api_key = os.environ[exp.model.api_key_env]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = {
            "model": "deepseek-v4-flash",
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Perform a web search for the query: Python official documentation itertools",
                        }
                    ],
                }
            ],
            "tools": [
                {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
            ],
        }
        with urlopen(
            Request(
                f"http://127.0.0.1:{server.server_port}/search/v1/messages",
                json.dumps(payload).encode(),
                {"Content-Type": "application/json", "x-api-key": "relay-only"},
            ),
            timeout=90,
        ) as response:
            data = json.load(response)
        blocks = [
            b
            for b in data.get("content", [])
            if b.get("type") == "web_search_tool_result"
        ]
        results = [
            r
            for b in blocks
            for r in b.get("content", [])
            if isinstance(r, dict) and r.get("type") == "web_search_result"
        ]
        if not results:
            raise RuntimeError("Search API did not return native search results")
        write(
            args.output / "search-check.json",
            {
                "passed": True,
                "results": len(results),
                "urls": [r.get("url") for r in results],
                "usage": data.get("usage"),
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    async def guardian_check():
        name = "guardian-live-check-" + uuid4().hex[:12]
        backend = DockerTarget(name)
        code = (
            "import pathlib,json,time; p=pathlib.Path('/logs/agent');p.mkdir(parents=True);"
            "(p/'events.jsonl').write_text(json.dumps({'type':'session_event','data':{'event':"
            "{'type':'tool/call','time':int(time.time()*1000),'data':{'callId':'legal-wait','name':'job_output',"
            "'arguments':{'job_id':'fixture','wait':True,'timeout_ms':600000}}}}})+'\\n');time.sleep(600)"
        )
        await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "run",
                "-d",
                "--pull",
                "never",
                "--network",
                "none",
                "--label",
                "com.docker.compose.project=" + backend.project,
                "--label",
                "com.docker.compose.service=main",
                "python:3.12.13-slim-bookworm",
                "python",
                "-c",
                code,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        try:
            incident = args.output / "incidents" / "legitimate-wait"
            await capture(
                incident,
                backend,
                {
                    "reason": "no_observed_progress",
                    "agent": "dsh-standard",
                    "remaining_agent_seconds": 500,
                    "fixture": "这是隔离的守护诊断验收容器。当前 job_output 的 600 秒等待刚刚开始，没有逾期；请核查现场后记录决策。",
                    "monitor": {
                        "telemetry_complete": True,
                        "pending": [{"name": "job_output", "timeout": 600}],
                    },
                },
            )
            cfg = exp.guardian.model_copy(
                update={"diagnostic_seconds": 300, "max_actions": 8}
            )
            result = await run_driver(cfg, incident, backend, lambda: True)
            write(incident / "driver-result.json", result)
            actions = [
                json.loads(line)
                for line in (incident / "actions.jsonl").read_text().splitlines()
            ]
            inspected = any(r.get("kind") == "result" for r in actions)
            if (
                result.get("assessment", {}).get("decision") != "continue"
                or not inspected
            ):
                raise RuntimeError(
                    "Guardian did not inspect and correctly continue the legitimate wait"
                )
            write(
                args.output / "guardian-check.json",
                {
                    "passed": True,
                    "diagnosis": read(incident / "diagnosis.json"),
                    "actions": len(actions),
                },
            )
        finally:
            await backend.stop()

    asyncio.run(guardian_check())
    write(
        args.output / "result.json", {"passed": True, "search": True, "guardian": True}
    )
    print(json.dumps({"passed": True, "output": str(args.output)}))


if __name__ == "__main__":
    main()
