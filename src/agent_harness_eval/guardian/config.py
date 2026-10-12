from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def validate_tasks(paths):
    from harbor.models.task.config import TaskConfig
    from harbor.models.task.verifier_mode import task_has_any_separate_verifier

    for path in paths:
        task = TaskConfig.model_validate_toml((path / "task.toml").read_text())
        if task.steps or task_has_any_separate_verifier(task):
            raise ValueError(
                "Guardian currently requires single-step tasks with a shared verifier environment"
            )


class GuardianConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["observe", "assist"] = "observe"
    command: list[str] = Field(default_factory=list)
    version: str | None = None
    pass_env: list[str] = Field(default_factory=list)
    poll_seconds: float = Field(default=15, ge=0.1, le=300)
    idle_seconds: float = Field(default=300, ge=1)
    confirmations: int = Field(default=2, ge=1, le=10)
    tool_grace_seconds: float = Field(default=45, ge=0)
    diagnostic_seconds: float = Field(default=120, ge=1)
    verification_seconds: float = Field(default=45, ge=1)
    command_seconds: float = Field(default=20, ge=1, le=120)
    cleanup_seconds: float = Field(default=30, ge=1, le=120)
    setup_seconds: float = Field(default=1200, ge=1)
    verifier_seconds: float = Field(default=1200, ge=1)
    max_incidents: int = Field(default=3, ge=1, le=20)
    max_actions: int = Field(default=12, ge=1, le=100)

    @model_validator(mode="after")
    def driver_contract(self):
        if self.mode == "assist" and (not self.command or not self.version):
            raise ValueError("assist requires a guardian command and explicit version")
        if self.mode == "observe" and self.command:
            raise ValueError("observe does not execute a guardian command")
        if any(not arg or "\x00" in arg for arg in self.command):
            raise ValueError("guardian command arguments must be nonempty")
        if any(not name.isidentifier() for name in self.pass_env):
            raise ValueError("pass_env contains environment variable names, not values")
        return self
