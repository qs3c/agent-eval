from pathlib import Path

import pytest

from agent_harness_eval.config import Benchmark, load
from agent_harness_eval.benchmarks.registry import resolve


def test_generic_resolved_is_not_swe_and_unsupported_requirements_are_rejected():
    benchmark = Benchmark(id="custom", tasks=[Path("tasks/file-create")], score_kind="resolved")
    assert resolve(benchmark).id == "harbor-tasks"
    benchmark.requires = {"simulated-user"}
    with pytest.raises(ValueError, match="unsupported"):
        resolve(benchmark).resolve(benchmark)


def test_known_swe_identity_and_controls_use_registered_lifecycle():
    benchmark = Benchmark(id="swe", tasks=[Path("tasks/file-create")],
                          dataset={"name": "princeton-nlp/SWE-bench_Verified", "revision": "fixture", "split": "test"})
    assert resolve(benchmark).id == "swe"
    exp = load(Path("experiments/controls.json"))
    plugin = resolve(exp.benchmark)
    value = plugin.resolve(exp.benchmark)
    assert value.task_ids == ("file-create", "file-preserve")
    assert len(value.task_hashes) == 2
