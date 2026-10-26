from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from ..workspace.models import Frozen
from ..policies.model import ModelPolicy


class PlatformAgentRequest(Frozen):
    role: Literal["integration", "guardian", "diagnosis", "improvement"]
    workspace: Path
    output: Path
    evidence_refs: tuple[str, ...] = ()
    model_policy: ModelPolicy | None = None
    timeout_seconds: float = Field(gt=0, le=86400)
    max_actions: int = Field(default=100, gt=0, le=1000)
    tools: tuple[str, ...] = ()
    cpus: int = Field(default=1, ge=1)
    memory_mb: int = Field(default=512, ge=256)

    @model_validator(mode="after")
    def scope(self):
        from .roles import TOOLS

        if self.model_policy and self.model_policy.role != self.role:
            raise ValueError("Platform model role mismatch")
        if not set(self.tools) <= TOOLS[self.role]:
            raise ValueError("Tool exceeds platform role authority")
        if self.output.is_symlink() or self.workspace.is_symlink():
            raise ValueError("Platform directories must not be symlinks")
        return self
