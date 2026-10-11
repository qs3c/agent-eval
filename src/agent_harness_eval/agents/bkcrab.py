import shlex
from pathlib import Path

from ..provenance import digest
from .native import Native


class Bkcrab(Native):
    @staticmethod
    def name():
        return "bkcrab"

    def __init__(self, *args, binary, **kwargs):
        super().__init__(*args, **kwargs)
        self.binary = Path(binary)
        if digest(self.binary) != self.identity["binary_sha256"]:
            raise ValueError("BKCrab binary/provenance mismatch")

    async def install(self, environment):
        await environment.upload_file(self.binary, "/installed-agent/bkcrab-eval")
        await self.exec_as_root(environment, "chmod 755 /installed-agent/bkcrab-eval")
        await self.model_setup(environment)

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
        await self.exec_as_agent(
            environment,
            self.wrap_model(
                command + " >/logs/agent/stdout.log 2>/logs/agent/stderr.log"
            ),
            env={"EVAL_MODEL_API_KEY": "relay-only"},
            cwd=work,
        )
