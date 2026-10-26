"""One adapter for CLI/Python recipes; the agent retains its own harness loop."""

import json
import shlex
import tempfile
import time
from pathlib import Path

from ..integrations.builder import unpack
from ..integrations.schema import Recipe as Contract, expand
from ..provenance import digest
from ..workspace.models import AgentRelease, identity
from .native import Native


class Recipe(Native):
    supported_model_modes = frozenset({"offline", "mock", "live"})

    @staticmethod
    def name():
        return "recipe"

    def __init__(self, *args, bundle, **kwargs):
        super().__init__(*args, **kwargs)
        self.bundle = Path(bundle)
        self.recipe = Contract.model_validate(self.identity["recipe"])
        release = AgentRelease.model_validate(self.identity["release"])
        if (identity(release) != self.identity["release_id"] or release.recipe_digest != self.recipe.digest
                or digest(self.bundle) != release.artifact_digests["bundle"]):
            raise ValueError("Release recipe/artifact identity mismatch")
        if self.model_config["mode"] not in self.recipe.model_modes:
            raise ValueError("Recipe does not support selected model mode")
        self.identity["version"] = self.identity["release_id"]

    async def install(self, environment):
        # Validate members on the host before asking container tar to extract them.
        with tempfile.TemporaryDirectory() as tmp:
            unpack(self.bundle.read_bytes(), tmp)
        await environment.upload_file(self.bundle, "/installed-agent/agent.tar")
        await self.exec_as_root(environment, "mkdir -p /installed-agent/recipe && tar -xf /installed-agent/agent.tar -C /installed-agent/recipe && chmod -R a+rX /installed-agent/recipe")
        await environment.upload_file(Path(__file__).parents[1] / "resources" / "recipe_entry.py", "/installed-agent/recipe_entry.py")
        if self.model_config["mode"] != "offline":
            await self.model_setup(environment)

    async def run(self, instruction, environment, context):
        work = await self.prepare(environment, instruction)
        values = {"agent_dir": "/installed-agent/recipe", "workspace": work,
                  "request_file": "/installed-agent/request.json", "prompt_file": "/installed-agent/prompt.txt",
                  "output_dir": "/logs/agent", "model_id": self.model_config["name"],
                  "api_base": "http://127.0.0.1:18765/v1", "home": "/tmp/recipe-agent-home"}
        request = {"schema_version": 1, "instruction": instruction, "workspace": work,
                   "output_dir": values["output_dir"], "deadline_unix": time.time() + self.timeout_seconds,
                   "model": {"mode": self.model_config["mode"], "id": values["model_id"], "api_base": values["api_base"],
                             "api_key_env": list(self.recipe.model_key_env), "max_tokens": self.model_config["max_tokens"],
                             "context_window": self.model_config["context_window"]}}
        path = self.logs_dir / "request.json"
        path.write_text(json.dumps(request))
        await environment.upload_file(path, values["request_file"])
        argv = expand(self.recipe.entrypoint.argv, values) if self.recipe.entrypoint.argv else [
            "python3", "/installed-agent/recipe_entry.py", self.recipe.entrypoint.python, values["request_file"]]
        env = {key: expand([value], values)[0] for key, value in self.recipe.environment.items()}
        env.update({key: "relay-only" for key in self.recipe.model_key_env})
        env["HOME"] = values["home"]
        env.setdefault("PYTHONPATH", values["agent_dir"])
        # A bundled interpreter's PYTHONHOME/LD_LIBRARY_PATH must not contaminate
        # the platform relay or its readiness probe, which use the task runtime.
        command = "mkdir -p /tmp/recipe-agent-home && " + shlex.join(
            ["env", *[f"{key}={value}" for key, value in env.items()], *argv]
        ) + " >/logs/agent/stdout.log 2>/logs/agent/stderr.log"
        if self.model_config["mode"] != "offline":
            command = self.wrap_model(command)
        result = await self.exec_as_agent(environment, command, cwd=work)
        if result.return_code != 0:
            raise RuntimeError("Recipe agent exited with code " + str(result.return_code))
        for source, target in ((self.recipe.trace, "events.jsonl"), (self.recipe.usage, "declared-usage.json")):
            if source and source != target:
                copied = await self.exec_as_agent(environment, shlex.join(["cp", "--", "/logs/agent/" + source, "/logs/agent/" + target]))
                if copied.return_code:
                    raise ValueError("Declared evidence output is missing: " + source)

    def populate_context_post_run(self, context):
        from ..integrations.evidence import normalize

        normalize(self.logs_dir, self.recipe)
        super().populate_context_post_run(context)
        path = self.logs_dir / "agent-result.json"
        if not path.is_file():
            raise ValueError("Recipe did not emit agent-result.json")
        result = json.loads(path.read_text())
        if result.get("schema_version") != 1 or result.get("status") not in {"completed", "failed"}:
            raise ValueError("Invalid recipe result contract")
        context.metadata["release_id"] = self.identity["release_id"]
        context.metadata["recipe_result"] = result
        if result["status"] == "failed":
            raise RuntimeError("Recipe reported failure")
