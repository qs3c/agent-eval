from harbor.agents.installed.base import BaseInstalledAgent


class Control(BaseInstalledAgent):
    """Deterministic fixture agent. NOT a model baseline or benchmark solver."""

    supported_model_modes = frozenset({"offline"})

    @staticmethod
    def name():
        return "fixture-control"

    def __init__(self, *args, succeed=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.succeed = succeed

    async def install(self, environment):
        pass

    async def run(self, instruction, environment, context):
        if self.succeed:
            await self.exec_as_agent(environment, "printf 'ready\\n' > result.txt")
        context.metadata = {
            "evidence_kind": "deterministic fixture",
            "success_control": self.succeed,
        }
