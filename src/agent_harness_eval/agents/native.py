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

    def __init__(self, *args, model_config, timeout_seconds=300, provenance, **kwargs):
        super().__init__(*args, **kwargs)
        self.model_config = model_config
        self.timeout_seconds = timeout_seconds
        self.provenance = Path(provenance)
        self.identity = json.loads(self.provenance.read_text())

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
            with tempfile.TemporaryDirectory(prefix="agent-harness-eval-secret-") as tmp:
                secret = Path(tmp) / "key"
                secret.touch(mode=0o600)
                secret.write_text(os.environ[self.model_config["api_key_env"]])
                await environment.upload_file(secret, "/installed-agent/live.key")
                config = Path(tmp) / "model.json"
                config.write_text(json.dumps(self.model_config))
                await environment.upload_file(config, "/installed-agent/model.json")
            owner = shlex.quote(str(environment.default_user or "root"))
            await self.exec_as_root(
                environment,
                f"chown {owner} /installed-agent/live.key && chmod 600 /installed-agent/live.key",
            )

    def wrap_model(self, command):
        ready = "import socket,time\nfor _ in range(100):\n try:\n  socket.create_connection(('127.0.0.1',18765),.1).close(); break\n except OSError: time.sleep(.05)\nelse: raise RuntimeError('model relay did not start')"
        return (
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
            "evidence_kind": "live official DeepSeek"
            if self.model_config["mode"] == "live"
            else "mock provider; not model performance",
        }
        (self.logs_dir / "coverage.json").write_text(json.dumps(coverage, indent=2))
