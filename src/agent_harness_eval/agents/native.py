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
        if self.model_config.get("policy") is not None:
            from ..policies.model import freeze

            policy = freeze(self.model_config, "agent")
            await environment.upload_file(source, "/installed-agent/policy_upstream.py")
            await environment.upload_file(source, "/logs/agent/policy_upstream.py")
            for name in ("policy_runtime.py", "policy_model.py"):
                await environment.upload_file(source.with_name(name), "/installed-agent/" + name)
            wrapper = source.with_name("policy_model.py")
            await environment.upload_file(wrapper, "/installed-agent/model_server.py")
            await environment.upload_file(wrapper, "/logs/agent/model_server.py")
            with tempfile.TemporaryDirectory(prefix="harness-policy-") as tmp:
                path = Path(tmp) / "model-policy.json"
                path.write_text(json.dumps({"policy": policy.model_dump(mode="json"), "digest": policy.digest}))
                await environment.upload_file(path, "/installed-agent/model-policy.json")
                await environment.upload_file(path, "/logs/agent/model-policy.json")
        else:
            await environment.upload_file(source, "/installed-agent/model_server.py")
            await environment.upload_file(source, "/logs/agent/model_server.py")
        if live:
            await environment.upload_file(source.with_name("search_review.py"), "/installed-agent/search_review.py")
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
            "evidence_kind": "live model: " + self.model_config.get("api_base", "https://api.deepseek.com")
            if self.model_config["mode"] == "live"
            else "mock provider; not model performance",
        }
        (self.logs_dir / "coverage.json").write_text(json.dumps(coverage, indent=2))
        if self.model_config.get("policy") is not None:
            from ..policies.model import freeze, verify_usage
            from ..traces import read_lines

            report = verify_usage(freeze(self.model_config, "agent"), {
                "requests": read_lines(self.logs_dir / "policy-requests.jsonl"), "cost_usd": context.cost_usd})
            (self.logs_dir / "model-policy-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            context.metadata["model_policy"] = report
