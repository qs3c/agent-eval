"""Lifecycle boundary; executors and benchmarks have independent contracts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..workspace.models import BenchmarkRevision


@dataclass(frozen=True)
class Dependency:
    resource_id: str
    kind: str
    owned: bool = False


@dataclass(frozen=True)
class Prepared:
    tasks: tuple[Path, ...]
    dependencies: tuple[Dependency, ...] = ()


class BenchmarkPlugin(Protocol):
    id: str
    def resolve(self, benchmark) -> BenchmarkRevision: ...
    def materialize(self, paths, selection, output) -> list[Path]: ...
    def prepare(self, experiment, output, paths, number) -> Prepared: ...
    def collect(self, output) -> dict: ...
    def grade(self, experiment, output, paths, number): ...
    def release(self, experiment, output, paths, number): ...
