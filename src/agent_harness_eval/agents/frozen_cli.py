"""Frozen native CLIs: transport configuration only, no replacement agent loop."""

import json
import shlex
import tempfile
from pathlib import Path

from ..provenance import digest
from ..web_proxy import web_proxy_environment
from .native import Native


def isolated_python_module(executable, module):
    """Isolate the Agent interpreter, while preserving the task's working directory.

    -P excludes cwd/script paths; -s excludes user site packages. These flags
    apply only to the Agent process, not Python commands issued by its tools.
    PYTHONPATH is supplied explicitly by the adapter for the frozen runtime.
    """
    return f"env -u PYTHONHOME {shlex.quote(executable)} -sP -m {shlex.quote(module)}"


class FrozenCLI(Native):
    def __init__(self, *args, bundle, **kwargs):
        super().__init__(*args, **kwargs)
        self.bundle = Path(bundle)
        if self.identity.get("agent") != self.name() or digest(self.bundle) != self.identity["bundle_sha256"]:
            raise ValueError("Native CLI bundle/provenance mismatch")

    def populate_context_post_run(self, context):
        from ..native_cli_events import collect_events
        collect_events(self.logs_dir, self.name())
        super().populate_context_post_run(context)

    async def install(self, environment):
        await environment.upload_file(self.bundle, "/installed-agent/native.tar.gz")
        await self.exec_as_root(environment, "mkdir -p /installed-agent/native && tar -xzf /installed-agent/native.tar.gz -C /installed-agent/native && chmod -R a+rX /installed-agent/native")
        await self.model_setup(environment)
        await self.search_setup(environment)

    async def put_config(self, environment, config):
        with tempfile.TemporaryDirectory(prefix="native-cli-config-") as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(config, ensure_ascii=False, indent=2))
            await environment.upload_file(path, "/logs/agent/effective-config.json")

    def home_environment(self):
        home = "/tmp/" + self.name() + "-eval-home"
        return {"HOME": home, "XDG_CONFIG_HOME": home + "/.config", "XDG_CACHE_HOME": home + "/.cache",
                "XDG_DATA_HOME": "/logs/agent/native-data", "XDG_STATE_HOME": "/logs/agent/native-state"}

    async def execute(self, environment, work, command, env):
        if self.web_search:
            # Read only inside the container; never put the key in logged env/config.
            command = 'export EXA_API_KEY="$(cat /installed-agent/search.key)" && rm /installed-agent/search.key && ' + command
        async with web_proxy_environment(self, environment) as proxy:
            await self.exec_as_agent(environment, self.wrap_model(command), env={**env, **proxy}, cwd=work)


class OpenCode(FrozenCLI):
    @staticmethod
    def name():
        return "opencode"

    def configuration(self):
        model = self.model_config
        return {"$schema": "https://opencode.ai/config.json", "autoupdate": False,
                "model": "deepseek/" + model["name"], "permission": "allow",
                "provider": {"deepseek": {"options": {"baseURL": "http://127.0.0.1:18765/v1", "apiKey": "relay-only"},
                    "models": {model["name"]: {"name": model["name"], "tool_call": True,
                        "limit": {"context": model["context_window"], "output": model["max_tokens"]}}}}}}

    async def run(self, instruction, environment, context):
        work = await self.prepare(environment, instruction)
        await self.put_config(environment, self.configuration())
        env = {**self.home_environment(), "OPENCODE_CONFIG": "/logs/agent/effective-config.json",
               "DEEPSEEK_API_KEY": "relay-only", "OPENCODE_DISABLE_AUTOUPDATE": "true",
               "OPENCODE_ENABLE_EXA": "true", "OPENCODE_WEBSEARCH_PROVIDER": "exa"}
        command = 'mkdir -p "$HOME" && /installed-agent/native/opencode run --print-logs --format json --model ' + shlex.quote("deepseek/" + self.model_config["name"]) + ' --auto "$(cat /installed-agent/prompt.txt)" >/logs/agent/opencode.jsonl 2>/logs/agent/stderr.log'
        await self.execute(environment, work, command, env)


class Hermes(FrozenCLI):
    @staticmethod
    def name():
        return "hermes"

    def configuration(self):
        return {"model": {"default": self.model_config["name"], "provider": "custom",
                          "base_url": "http://127.0.0.1:18765/v1", "api_mode": "chat_completions",
                          "context_length": self.model_config["context_window"]},
                "toolsets": ["hermes-cli"], "terminal": {"backend": "local"},
                "web": {"backend": "exa", **({"provider_tier": {"exa": "paid"}, "keyless_fallback": False, "keyless_rescue": False} if getattr(self, "web_search", None) else {})}}

    async def run(self, instruction, environment, context):
        work = await self.prepare(environment, instruction)
        await self.put_config(environment, self.configuration())
        env = {**self.home_environment(), "HERMES_HOME": "/logs/agent/hermes-home", "OPENAI_API_KEY": "relay-only",
               "OPENAI_BASE_URL": "http://127.0.0.1:18765/v1", "HERMES_DISABLE_LAZY_INSTALLS": "1",
               "PYTHONPATH": "/installed-agent/native/source:/installed-agent/native/site-packages",
               "HERMES_INSTALL_ROOT": "/installed-agent/native/source"}
        executable = isolated_python_module("/installed-agent/native/python/bin/python3", "hermes_cli.main")
        command = 'mkdir -p "$HOME" "$HERMES_HOME" && cp /logs/agent/effective-config.json "$HERMES_HOME/config.yaml" && ' + executable + ' --yolo chat -Q --provider custom --model ' + shlex.quote(self.model_config["name"]) + ' -q "$(cat /installed-agent/prompt.txt)" >/logs/agent/stdout.log 2>/logs/agent/stderr.log'
        # State is inside this trial's log mount: fresh per task and retained as raw evidence.
        await self.execute(environment, work, command, env)
