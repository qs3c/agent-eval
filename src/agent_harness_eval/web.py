"""Same-origin local API and static console."""

import re
from contextlib import asynccontextmanager
from ipaddress import ip_address
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .console import Console, redact
from .console import read_json as read_json_log


def create_app(root: Path, allow_live=False, recover=False, *, bind_host="127.0.0.1"):
    address = ip_address(bind_host)
    if address.is_unspecified:
        raise ValueError("请指定具体监听 IP，不能使用通配地址")
    authority = f"[{address}]" if address.version == 6 else str(address)
    allowed_hosts = {"localhost", "127.0.0.1", "[::1]", authority}
    host_pattern = re.compile(
        "(?:" + "|".join(re.escape(host) for host in sorted(allowed_hosts)) + r")(?::\d{1,5})?"
    )
    console = Console(root, allow_live=allow_live)

    @asynccontextmanager
    async def lifespan(app):
        from .operations.manager import ensure

        if recover:
            from .operations.commands import Commands

            commands = Commands(root)
            for job in commands.list():
                if job["status"] == "interrupted":
                    commands.acknowledge_interruption(job["id"])
        ensure(root, allow_live=allow_live)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.console = console
    from .operations.commands import Commands

    @app.get("/api/operations")
    def operations():
        return {"operations": Commands(root).list()}

    @app.post("/api/operations/{job_id}/{action}")
    def control_operation(job_id: str, action: str):
        return Commands(root).control(job_id, action)
    from .rsi.api import router

    app.include_router(router(console))
    from .model_pool import router as model_router

    app.include_router(model_router(console.root))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic input/context can contain a submitted API key.
        return JSONResponse({"detail": "配置字段无效，请检查名称、接口地址、Token 数值和必填项"}, status_code=422)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        if not host_pattern.fullmatch(host):
            return JSONResponse({"detail": "Host 不在允许列表中"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{host}":
                return JSONResponse({"detail": "拒绝跨站写入"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "拒绝跨站写入"}, status_code=403)
            if (
                request.headers.get("content-type", "").split(";")[0]
                != "application/json"
            ):
                return JSONResponse({"detail": "请求必须为 JSON"}, status_code=415)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({"detail": "请求过大"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        return JSONResponse({"detail": redact(str(exc))}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "找不到运行"}, status_code=404)

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request, exc):
        return JSONResponse({"detail": "模板或依赖文件不存在"}, status_code=422)

    @app.get("/api/templates")
    def templates():
        return {
            "templates": console.templates(),
            "allow_live": allow_live,
            "blocked": console.blocked,
        }

    @app.get("/api/runs")
    def runs():
        return {"runs": console.runs(), "blocked": console.blocked}

    @app.post("/api/runs", status_code=201)
    def submit(data: dict):
        return console.submit(data)

    @app.get("/api/runs/{run_id}")
    def detail(run_id: str):
        return console.detail(run_id)

    @app.post("/api/runs/{run_id}/batches/{number}/regrade", status_code=202)
    def submit_regrading(run_id: str, number: int):
        return console.submit_regrading(run_id, number)

    @app.get("/api/runs/{run_id}/batches/{number}/regrade-log")
    def regrade_log(run_id: str, number: int):
        from .console import safe_file
        from .guardian.evidence import log_tail

        output = safe_file(console.artifacts, Path(console.row(run_id)["path"]))
        directory = safe_file(output, output / "batches" / f"{number:03d}" / "official")
        progress = read_json_log(output, directory / "progress.json")
        logs = []
        for unit in (progress or {}).get("units", {}).values():
            if unit.get("log"):
                logs.append({"task": unit["task"], "agent": unit["agent"],
                             **log_tail(safe_file(output, output / unit["log"]))})
        if not progress:
            for path in directory.glob("*/attempt-*/console.log"):
                logs.append({"task": "旧版批次日志", "agent": path.parent.parent.name,
                             **log_tail(safe_file(output, path))})
        for item in logs:
            if "text" in item:
                item["text"] = redact(item["text"])
        return {"logs": logs}

    @app.get("/api/runs/{run_id}/logs")
    def logs(run_id: str):
        return {"text": console.logs(run_id)}

    @app.get("/api/runs/{run_id}/comparison")
    def comparison(run_id: str):
        return console.comparison(run_id)

    @app.get("/api/runs/{run_id}/incidents")
    def incident_list(run_id: str):
        from .console import safe_file
        from .guardian.views import incidents

        root = safe_file(console.artifacts, Path(console.row(run_id)["path"]))
        return incidents(root)

    @app.get("/api/runs/{run_id}/incidents/{incident_id}")
    def incident_detail(run_id: str, incident_id: str):
        from .console import safe_file
        from .guardian.views import detail

        root = safe_file(console.artifacts, Path(console.row(run_id)["path"]))
        return detail(root, incident_id)

    @app.get("/api/runs/{run_id}/traces")
    def traces(run_id: str):
        from .trace_view import trace_list

        return trace_list(console, run_id)

    @app.get("/api/runs/{run_id}/traces/{trial_id}")
    def trial_trace(run_id: str, trial_id: str):
        from .trace_view import trace

        return trace(console, run_id, trial_id)

    @app.get("/api/compare")
    def compare(left: str, right: str):
        return console.compare(left, right)

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
