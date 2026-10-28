import json
import os
import shlex
import ssl
import tempfile
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent

from ..traces import convert
from ..search import WebSearch


class Native(BaseInstalledAgent):
    supported_model_modes = frozenset({"mock", "live"})
    accepts_model_config = True

    def __init__(self, *args, model_config, timeout_seconds=300, provenance, web_proxy="device", web_search=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.model_config = model_config
        self.timeout_seconds = timeout_seconds
        self.web_proxy = web_proxy
        self.web_search = WebSearch.model_validate(web_search) if web_search is not None else None
        self.provenance = Path(provenance)
        self.identity = json.loads(self.provenance.read_text())

    async def prepare(self, environment, instruction):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        prompt = self.logs_dir / "instruction.txt"
        prompt.write_text(instruction)
        await environment.upload_file(prompt, "/installed-agent/prompt.txt")
        await environment.upload_file(self.provenance, "/logs/agent/provenance.json")
        work = await self.exec_as_agent(environment, "pwd")
        helper = Path(__file__).parents[1] / "resources/capture_patch.py"
        await environment.upload_file(helper, "/installed-agent/capture_patch.py")
        # Capture after native installation but before any model/tool execution.
        frozen = await self.exec_as_agent(environment,
            "if [ -d /testbed/.git ]; then python3 /installed-agent/capture_patch.py --freeze --repo /testbed --baseline /logs/agent/swe-baseline.json; fi")
        if frozen.return_code:
            raise RuntimeError("SWE initial workspace snapshot failed")
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
            await environment.upload_file(source.with_name("fetch_review.py"), "/installed-agent/fetch_review.py")
            await environment.upload_file(source.with_name("tool_result_review.py"), "/installed-agent/tool_result_review.py")
            with tempfile.TemporaryDirectory(prefix="agent-harness-eval-secret-") as tmp:
                trust = Path(tmp) / "model-ca.pem"
                trust.write_text("".join(
                    ssl.DER_cert_to_PEM_cert(cert)
                    for cert in ssl.create_default_context().get_ca_certs(binary_form=True)
                ))
                if not trust.stat().st_size:
                    raise ValueError("Host default TLS trust store is empty")
                await environment.upload_file(trust, "/installed-agent/model-ca.pem")
                await environment.upload_file(trust, "/logs/agent/model-ca.pem")
                secret = Path(tmp) / "key"
                secret.touch(mode=0o600)
                secret.write_text(os.environ[self.model_config["api_key_env"]])
                await environment.upload_file(secret, "/installed-agent/live.key")
                config = Path(tmp) / "model.json"
                config_value = self.model_config.copy()
                if getattr(self, "web_search", None) and self.web_search.transport == "platform-relay":
                    config_value["web_search"] = {"provider": "exa", "transport": "platform-relay"}
                config.write_text(json.dumps(config_value))
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

    async def search_setup(self, environment):
        if self.web_search and self.web_search.credential_env:
            value = os.environ.get(self.web_search.credential_env, "").strip()
            if not value:
                raise ValueError("Missing web search credential environment variable: " + self.web_search.credential_env)
            with tempfile.TemporaryDirectory(prefix="eval-search-") as tmp:
                path = Path(tmp) / "search.key"
                path.touch(mode=0o600)
                path.write_text(value)
                await environment.upload_file(path, "/installed-agent/search.key")
            owner = shlex.quote(str(environment.default_user or "root"))
            await self.exec_as_root(environment, f"chown {owner} /installed-agent/search.key && chmod 600 /installed-agent/search.key")

    def wrap_model(self, command):
        trust_env = "SSL_CERT_FILE=/installed-agent/model-ca.pem " if self.model_config.get("mode") == "live" else ""
        ready = "import socket,time\nfor _ in range(100):\n try:\n  socket.create_connection(('127.0.0.1',18765),.1).close(); break\n except OSError: time.sleep(.05)\nelse: raise RuntimeError('model relay did not start')"
        return (
            "env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy "
            + trust_env
            + "python3 /installed-agent/model_server.py >/logs/agent/model-server.log 2>&1 & fixture_pid=$!; "
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
