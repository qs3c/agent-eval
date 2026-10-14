import shlex
from pathlib import Path

from ..provenance import digest
from .native import Native


class DeepSeekStandard(Native):
    @staticmethod
    def name():
        return "dsh-standard"

    def __init__(self, *args, bundle, **kwargs):
        super().__init__(*args, **kwargs)
        self.bundle = Path(bundle)
        if digest(self.bundle) != self.identity["bundle_sha256"]:
            raise ValueError("DSH bundle/provenance mismatch")
        if (
            self.identity.get("preset") != "standard"
            or self.identity.get("config_audit") != "passed"
        ):
            raise ValueError("DSH Standard audit required")

    async def install(self, environment):
        await environment.upload_file(self.bundle, "/installed-agent/dsh.tar.gz")
        await self.exec_as_root(
            environment,
            "mkdir -p /installed-agent/dsh && tar -xzf /installed-agent/dsh.tar.gz -C /installed-agent/dsh && chmod -R a+rX /installed-agent/dsh",
        )
        await self.model_setup(environment)

    async def run(self, instruction, environment, context):
        work = await self.prepare(environment, instruction)
        args = [
            "/installed-agent/dsh/node",
            "/installed-agent/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js",
            "--profile",
            "sdk",
            "--patch",
            "/installed-agent/dsh/transport.patch.yml",
            "--patch",
            "/installed-agent/dsh/standard.patch.yml",
        ]
        env = {
            "DSH_HOME": "/tmp/dsh-eval-home",
            "DSH_PERMISSION_MODE": "danger-full-access",
            "DEEPSEEK_API_KEY": "relay-only",
            "DEEPSEEK_BASE_URL": "http://127.0.0.1:18765",
            "DEEPSEEK_SEARCH_BASE_URL": "http://127.0.0.1:18765/search/v1",
            "EVAL_MODEL": self.model_config["name"],
            "EVAL_MAX_TOKENS": str(self.model_config["max_tokens"]),
            "DSH_PRIMARY_RUNTIME": "",
            "DSH_TOOLS_MODE": "native",
        }
        command = shlex.join(args)
        # The full expanded configuration is retained before the native loop starts.
        launch = self.wrap_model(
            command
            + " --dump-config > /logs/agent/effective-config.yml && "
            + command
            + " >/logs/agent/stdout.log 2>/logs/agent/stderr.log"
        )
        await self.exec_as_agent(environment, launch, env=env, cwd=work)
