"""Small explicit registry, not arbitrary imports from user configuration."""

from .harbor_tasks import HarborTasks


def resolve(benchmark, *, batch=None):
    from .swe import SWE

    selected = benchmark.plugin
    if selected is None:
        dataset = benchmark.dataset.name.lower() if benchmark.dataset else ""
        declared_swe_batch = batch and (batch.prepare_swe or batch.official_regrade)
        selected = "swe" if declared_swe_batch or "swe-bench" in dataset or all((path / "swe-provenance.json").is_file() for path in benchmark.tasks) else "harbor-tasks"
    return {"harbor-tasks": HarborTasks, "controls": HarborTasks, "swe": SWE}[selected]()
