import json
import os
import shlex
import tempfile
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent

from ..traces import convert


class Native(BaseInstalledAgent):
    supported_model_modes = frozenset({"mock", "live"})
    accepts_model_config = True

    def __init__(self, *args, model_config, timeout_seconds=300, provenance, web_proxy="device", **kwargs):
        super().__init__(*args, **kwargs)
        self.model_config = model_config
        self.timeout_seconds = timeout_seconds
        self.web_proxy = web_proxy
        self.provenance = Path(provenance)
        self.identity = json.loads(self.provenance.read_text())
        review = self.model_config.get("search_review")
        if review and review.get("web_fetch", True) and not self.identity.get("web_fetch_review"):
            raise ValueError("网页审查需要支持 web_fetch_review 的新 Agent 构建；不能静默使用旧包")

    async def prepare(self, environment, instruction):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        prompt = self.logs_dir / "instruction.txt"
        prompt.write_text(instruction)
        await environment.upload_file(prompt, "/installed-agent/prompt.txt")
        await environment.upload_file(self.provenance, "/logs/agent/provenance.json")
        work = await self.exec_as_agent(environment, "pwd")
        return work.stdout.strip()

    async def model_setup(self, environment):
        live = self.model_config["mode"] == "live"
        filename = "live_model.py" if live else "mock_model.py"
        source = Path(__file__).parents[1] / "resources" / filename
        await environment.upload_file(source, "/installed-agent/model_server.py")
        await environment.upload_file(source, "/logs/agent/model_server.py")
        if live:
            await environment.upload_file(source.with_name("search_review.py"), "/installed-agent/search_review.py")
            await environment.upload_file(source.with_name("fetch_review.py"), "/installed-agent/fetch_review.py")
            with tempfile.TemporaryDirectory(prefix="agent-harness-eval-secret-") as tmp:
                secret = Path(tmp) / "key"
                secret.touch(mode=0o600)
                secret.write_text(os.environ[self.model_config["api_key_env"]])
                await environment.upload_file(secret, "/installed-agent/live.key")
                config = Path(tmp) / "model.json"
                config.write_text(json.dumps(self.model_config))
                await environment.upload_file(config, "/installed-agent/model.json")
                review = self.model_config.get("search_review")
                if review:
                    review_key = Path(tmp) / "search-review.key"
                    review_key.touch(mode=0o600)
                    review_key.write_text(os.environ[review["api_key_env"]])
                    await environment.upload_file(review_key, "/installed-agent/search-review.key")
            owner = shlex.quote(str(environment.default_user or "root"))
            await self.exec_as_root(
                environment,
                f"chown {owner} /installed-agent/live.key && chmod 600 /installed-agent/live.key",
            )
            if self.model_config.get("search_review"):
                await self.exec_as_root(environment,
                    f"chown {owner} /installed-agent/search-review.key && chmod 600 /installed-agent/search-review.key")

    def wrap_model(self, command):
        review = self.model_config.get("search_review")
        if review and review.get("web_fetch", True):
            command = "(export EVAL_FETCH_REVIEW_URL=http://127.0.0.1:18765/review/web-fetch; " + command + ")"
        ready = "import socket,time\nfor _ in range(100):\n try:\n  socket.create_connection(('127.0.0.1',18765),.1).close(); break\n except OSError: time.sleep(.05)\nelse: raise RuntimeError('model relay did not start')"
        return (
            "env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy "
            "python3 /installed-agent/model_server.py >/logs/agent/model-server.log 2>&1 & fixture_pid=$!; "
            "trap 'kill $fixture_pid 2>/dev/null || true' EXIT; "
            + "python3 -c "
            + shlex.quote(ready)
            + " && "
            + command
        )

    def populate_context_post_run(self, context):
        trajectory, usage, coverage = convert(
            self.logs_dir,
            self.name(),
            self.identity.get("version", self.identity.get("source_sha256", "unknown")),
        )
        if trajectory:
            (self.logs_dir / "trajectory.json").write_text(
                trajectory.model_dump_json(indent=2, exclude_none=True)
            )
        context.n_input_tokens = usage.get("input_tokens")
        context.n_output_tokens = usage.get("output_tokens")
        context.cost_usd = 0.0 if self.model_config["mode"] == "mock" else None
        context.metadata = {
            "coverage": coverage,
            "evidence_kind": "live model: " + self.model_config.get("api_base", "https://api.deepseek.com")
            if self.model_config["mode"] == "live"
            else "mock provider; not model performance",
        }
        (self.logs_dir / "coverage.json").write_text(json.dumps(coverage, indent=2))
