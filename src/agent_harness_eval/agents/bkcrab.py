import os
import shlex
import tempfile
from pathlib import Path

from ..provenance import digest
from ..search import WebSearch
from ..web_proxy import web_proxy_environment
from .native import Native


class Bkcrab(Native):
    @staticmethod
    def name():
        return "bkcrab"

    def __init__(self, *args, binary, web_search=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.binary = Path(binary)
        self.web_search = WebSearch.model_validate(web_search) if web_search is not None else None
        if digest(self.binary) != self.identity["binary_sha256"]:
            raise ValueError("BKCrab binary/provenance mismatch")

    async def install(self, environment):
        search_key = None
        if self.web_search and self.web_search.credential_env:
            search_key = os.environ.get(self.web_search.credential_env, "").strip()
            if not search_key:
                raise ValueError("Missing web search credential environment variable: " + self.web_search.credential_env)
        await environment.upload_file(self.binary, "/installed-agent/bkcrab-eval")
        await self.exec_as_root(environment, "chmod 755 /installed-agent/bkcrab-eval")
        await self.model_setup(environment)
        if search_key:
            with tempfile.TemporaryDirectory(prefix="eval-search-") as tmp:
                key = Path(tmp) / "search.key"
                key.touch(mode=0o600)
                key.write_text(search_key)
                await environment.upload_file(key, "/installed-agent/search.key")
            if self.web_search.transport == "platform-relay":
                config = self.model_config.copy()
                config["web_search"] = {"provider": "exa", "transport": "platform-relay"}
                with tempfile.TemporaryDirectory(prefix="eval-search-config-") as tmp:
                    import json

                    path = Path(tmp) / "model.json"
                    path.write_text(json.dumps(config))
                    await environment.upload_file(path, "/installed-agent/model.json")
            owner = shlex.quote(str(environment.default_user or "root"))
            await self.exec_as_root(environment, f"chown {owner} /installed-agent/search.key && chmod 600 /installed-agent/search.key")

    async def run(self, instruction, environment, context):
        work = await self.prepare(environment, instruction)
        m = self.model_config
        command = shlex.join(
            [
                "/installed-agent/bkcrab-eval",
                "--workspace",
                work,
                "--home",
                "/tmp/bkcrab-eval-home",
                "--output",
                "/logs/agent",
                "--prompt-file",
                "/installed-agent/prompt.txt",
                "--model",
                m["name"],
                "--api-base",
                "http://127.0.0.1:18765/v1",
                "--max-tokens",
                str(m["max_tokens"]),
                "--context-window",
                str(m["context_window"]),
                "--timeout",
                str(self.timeout_seconds) + "s",
            ]
        )
        if self.web_search:
            command += " " + shlex.join(["--web-search-provider", self.web_search.provider])
            if self.web_search.endpoint:
                command += " " + shlex.join(["--web-search-endpoint", self.web_search.endpoint])
            if self.web_search.transport == "platform-relay":
                command += " --web-search-endpoint http://127.0.0.1:18765/search/exa"
            elif self.web_search.credential_env:
                command += " --web-search-key-file /installed-agent/search.key"
        async with web_proxy_environment(self, environment) as proxy:
            await self.exec_as_agent(
                environment,
                self.wrap_model(command + " >/logs/agent/stdout.log 2>/logs/agent/stderr.log"),
                env={"EVAL_MODEL_API_KEY": "relay-only", **proxy},
                cwd=work,
            )
