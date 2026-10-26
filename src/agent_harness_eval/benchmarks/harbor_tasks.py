"""Standard Harbor tasks: hidden verifier stays outside the Agent workspace."""

from pathlib import Path

from ..freezing import selection_record
from ..workspace.models import BenchmarkRevision
from .base import Dependency, Prepared
from .selection import inventory, materialize


class HarborTasks:
    id = "harbor-tasks"
    supported = frozenset({"filesystem", "shell"})

    def validate(self, benchmark):
        unsupported = benchmark.requires - self.supported
        if unsupported:
            raise ValueError("Benchmark runtime requirements unsupported: " + ", ".join(sorted(unsupported)))

    def resolve(self, benchmark):
        self.validate(benchmark)
        hashes = inventory(benchmark.tasks)
        return BenchmarkRevision(benchmark_id=benchmark.id, task_ids=tuple(hashes), task_hashes=hashes,
            dataset=benchmark.dataset.model_dump() if benchmark.dataset else None,
            scorer=benchmark.evaluation.model_dump() if benchmark.evaluation else None,
            selection=selection_record(benchmark.tasks))

    materialize = staticmethod(materialize)

    def prepare(self, experiment, output, paths, number):
        from ..batching import prepare_batch, batch_images

        if self.id != "swe" and experiment.batch and (experiment.batch.prepare_swe or experiment.batch.official_regrade):
            raise ValueError("SWE batch actions require the SWE benchmark plugin")
        prepare_batch(experiment, output, paths, number)
        images, _ = batch_images(output, paths, number)
        # Tags are references until the image lifecycle resolves an actual ID.
        return Prepared(tuple(paths), tuple(Dependency(value, "image-reference") for value in sorted(images)))

    def collect(self, output):
        from ..report import report

        return report(Path(output))

    def grade(self, experiment, output, paths, number):
        # A generic resolved metric never authorizes SWE's official scorer.
        if experiment.batch and experiment.batch.official_regrade:
            raise ValueError("Official SWE grading requires the SWE benchmark plugin")

    def release(self, experiment, output, paths, number):
        from ..batching import cleanup_batch

        return cleanup_batch(experiment, output, paths, number)
