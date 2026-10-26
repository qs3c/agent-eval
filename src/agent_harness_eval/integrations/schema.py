"""A recipe describes execution; it cannot grant its own conformance level."""

import re
from pathlib import Path, PurePosixPath
from string import Formatter
from typing import Literal

from pydantic import Field, model_validator

from ..workspace.models import Frozen, SourceRevision, identity

VARIABLES = {"agent_dir", "workspace", "request_file", "prompt_file", "output_dir", "model_id", "api_base", "home"}


def relative_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or value == ".":
        raise ValueError("Expected a contained relative path")
    return value


def expand(argv, values):
    return [value.format_map(values) for value in argv]


class Build(Frozen):
    artifact_origin: Literal["source", "registry-package"] = Field(default="source", exclude_if=lambda value: value == "source")
    image: str = Field(pattern=r"^(?:.+@)?sha256:[0-9a-f]{64}$")
    image_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$", exclude_if=lambda value: value is None)
    argv: tuple[str, ...] = Field(min_length=1)
    output: str = "dist"
    network: Literal["none", "bridge"] = "none"
    timeout_seconds: int = Field(default=600, gt=0, le=7200)
    cpus: int = Field(default=2, gt=0)
    memory_mb: int = Field(default=4096, gt=0)

    @model_validator(mode="after")
    def validate_paths(self):
        relative_path(self.output)
        if any("\x00" in arg for arg in self.argv):
            raise ValueError("NUL is not permitted in argv")
        return self


class Entrypoint(Frozen):
    argv: tuple[str, ...] | None = Field(default=None, min_length=1)
    python: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.argv is None) == (self.python is None):
            raise ValueError("Choose exactly one CLI argv or Python callable")
        for value in self.argv or ():
            for _, key, spec, conversion in Formatter().parse(value):
                if key is not None and (key not in VARIABLES or spec or conversion):
                    raise ValueError("Unsupported entrypoint substitution")
        return self


class Recipe(Frozen):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    source: SourceRevision
    build: Build
    entrypoint: Entrypoint
    profile: str = "default"
    patches: tuple[str, ...] = ()
    materialize_internal_links: bool = Field(default=False, exclude_if=lambda value: not value)
    environment: dict[str, str] = Field(default_factory=dict)
    model_key_env: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ("filesystem", "shell")
    model_modes: tuple[Literal["offline", "mock", "live"], ...] = ("mock", "live")
    model_protocol: Literal["openai-chat", "anthropic-messages", "none"] = "openai-chat"
    trace: str | None = None
    usage: str | None = None
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_contract(self):
        from urllib.parse import urlsplit

        if "://" in self.source.repository:
            url = urlsplit(self.source.repository)
            if url.password or url.query or url.fragment or (url.scheme in {"http", "https"} and url.username):
                raise ValueError("Repository URLs must not contain credentials or query parameters")
        if self.model_protocol == "none" and set(self.model_modes) != {"offline"}:
            raise ValueError("No-model protocol requires offline mode")
        for path in (*self.patches, *filter(None, (self.trace, self.usage))):
            relative_path(path)
        if len(set(self.patches)) != len(self.patches):
            raise ValueError("Duplicate patches")
        for key, value in self.environment.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or re.search(r"(^|_)(KEY|TOKEN|SECRET|PASSWORD|AUTH)(_|$)", key, re.I):
                raise ValueError("Credentials must use secret references")
            expand([value], {key: key for key in VARIABLES})
        for key in self.model_key_env:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError("Invalid model credential environment reference")
        return self

    @property
    def digest(self):
        # model_key_env contains environment names, never credentials.
        return identity(self)


def load(path):
    import yaml

    return Recipe.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
