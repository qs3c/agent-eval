"""Same-origin local API and static console."""

import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .console import Console, redact


def create_app(root: Path, allow_live=False, recover=False):
    console = Console(root, allow_live=allow_live)

    @asynccontextmanager
    async def lifespan(app):
        # Recovery acknowledgement is deliberately local CLI-only, never a browser action.
        console.start()
        if recover:
            with console.connect() as db:
                db.execute(
                    "UPDATE runs SET status='failed', error='用户已检查并处理遗留执行；该运行未重跑' WHERE status='interrupted'"
                )
            console.blocked = None
        try:
            yield
        finally:
            console.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.console = console

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        if not re.fullmatch(r"(localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?", host):
            return JSONResponse({"detail": "仅允许本机 Host"}, status_code=403)
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

    @app.get("/api/runs/{run_id}/logs")
    def logs(run_id: str):
        return {"text": console.logs(run_id)}

    @app.get("/api/runs/{run_id}/comparison")
    def comparison(run_id: str):
        return console.comparison(run_id)

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
