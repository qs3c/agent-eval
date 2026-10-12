import os
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Command(Strict):
    argv: list[str] = Field(min_length=1)
    timeout_seconds: float = Field(default=600, ge=1, le=86400)
    pass_env: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid(self):
        if any(not arg or "\0" in arg for arg in self.argv):
            raise ValueError("Command arguments must be nonempty and contain no NUL")
        if any(not name.isidentifier() for name in self.pass_env):
            raise ValueError("pass_env must contain variable names, not credentials")
        return self


class Adapter(Command):
    version: str = Field(min_length=1)


class Profile(Strict):
    repository: Path
    parent_ref: str = Field(default="HEAD", pattern=r"^[^-\s][^\s]*$")
    adapter: Adapter
    checks: list[Command] = Field(min_length=1)
    build: Command | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    evaluation: Path | None = None
    github_repository: str | None = Field(default=None, pattern=r"^[\w.-]+/[\w.-]+$")
    pr_base: str = Field(default="main", pattern=r"^[\w][\w./-]*$")

    @model_validator(mode="after")
    def artifacts_contract(self):
        if not set(self.artifacts) <= {"binary", "bundle", "provenance"}:
            raise ValueError("Unknown Agent build artifact key")
        for value in self.artifacts.values():
            if Path(value).is_absolute() or ".." in Path(value).parts or not value:
                raise ValueError("Artifacts must be relative paths inside build output")
        if self.artifacts and not self.build:
            raise ValueError("Artifacts require a build command")
        return self


def load_profile(root, name):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        raise ValueError("Invalid profile ID")
    directory = (Path(root) / "rsi-profiles").resolve()
    path = directory / (name + ".json")
    if path.is_symlink() or not path.resolve().is_relative_to(directory):
        raise ValueError("Profile path escapes its directory")
    profile = Profile.model_validate_json(path.read_text(encoding="utf-8"))
    profile.repository = (path.parent / profile.repository).resolve()
    if profile.evaluation:
        profile.evaluation = (path.parent / profile.evaluation).resolve()
    return profile


def environment(names):
    return {
        k: v
        for k, v in os.environ.items()
        if k
        in {"PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR", "TEMP"}
        | set(names)
    }
