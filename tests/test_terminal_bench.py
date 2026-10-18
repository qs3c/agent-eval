import asyncio
import json
from pathlib import Path

import pytest
from harbor.models.registry import DatasetMetadata
from harbor.models.task.id import GitTaskId, PackageTaskId
from harbor.tasks.client import BatchDownloadResult, TaskDownloadResult

from agent_harness_eval.benchmarks import terminal
from agent_harness_eval.config import Experiment, load
from agent_harness_eval.provenance import tree_manifest


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    source = tmp_path / "upstream"
    for name, gpus in (("alpha", 0), ("beta", 0), ("gpu-task", 1)):
        path = source / name
        (path / "tests").mkdir(parents=True)
        (path / "task.toml").write_text(
            "[agent]\ntimeout_sec = 777\n[verifier]\ntimeout_sec = 333\n"
            f"[environment]\ncpus = 3\nmemory_mb = 6144\ngpus = {gpus}\n"
        )
        (path / "instruction.md").write_text("Original task " + name)
        (path / "tests/test.sh").write_text(
            "#!/bin/sh\necho 1 > /logs/verifier/reward.txt\n"
        )
    identities = [
        GitTaskId(
            git_url="https://example.invalid/tasks.git",
            git_commit_id="a" * 40,
            path=name,
        )
        for name in ("alpha", "beta", "gpu-task")
    ]
    metadata = DatasetMetadata(
        name="terminal-bench", version="2.0", task_ids=identities
    )

    class Registry:
        async def get_dataset_metadata(self, reference):
            return metadata

    class Downloader:
        async def download_tasks(self, task_ids, output_dir, export):
            assert export is True
            return BatchDownloadResult(
                results=[
                    TaskDownloadResult(
                        path=source / terminal.task_name(identity),
                        download_time_sec=0,
                        cached=False,
                    )
                    for identity in task_ids
                ],
                total_time_sec=0,
            )

    monkeypatch.setattr(terminal.RegistryClientFactory, "create", lambda: Registry())
    monkeypatch.setattr(terminal, "PackageDatasetClient", Registry)
    monkeypatch.setattr(terminal, "TaskClient", Downloader)
    return source, metadata


def template():
    return Experiment.model_validate(
        {
            "name": "source",
            "agents": [
                {"id": "oracle", "implementation": "oracle", "version": "pinned"}
            ],
            "benchmark": {"id": "swe", "tasks": ["unused"], "score_kind": "resolved"},
            "batch": {"size": 3, "prepare_swe": True, "official_regrade": True},
            "guardian": {"mode": "observe"},
        }
    )


def prepare(tmp_path, **kwargs):
    return asyncio.run(
        terminal.prepare(
            "terminal-bench@2.0", tmp_path / "dataset", template(), **kwargs
        )
    )


def test_original_tasks_preserved_and_generated_config_loads(tmp_path, upstream):
    source, _ = upstream
    result = prepare(
        tmp_path, cpu_only=True, config_output=tmp_path / "experiments/terminal.json"
    )
    target = Path(result["dataset"])
    for name in ("alpha", "beta"):
        assert tree_manifest(source / name) == tree_manifest(target / name)
    selection = json.loads((target / "selection.json").read_text())
    assert result["selected"] == 2 and result["full_dataset"] is False
    assert selection["excluded"] == {"gpu-task": "任务需要 GPU/TPU；显式 CPU 子集筛选"}
    assert selection["tasks"]["alpha"]["upstream"]["git_commit_id"] == "a" * 40
    assert selection["tasks"]["alpha"]["files"]["tests/test.sh"]
    exp = load(Path(result["config"]))
    assert exp.cpus is exp.memory_mb is exp.timeout_seconds is None
    assert not exp.batch.prepare_swe and not exp.batch.official_regrade
    assert exp.guardian.mode == "observe" and exp.model.search_review is None
    assert exp.benchmark.dataset.revision == "2.0"
    assert selection["tasks"]["alpha"]["agent_timeout_sec"] == 777
    assert not (target / "alpha/tests/capture_patch.py").exists()
    # Existing experiment defaults retain their exact old meanings.
    original = template()
    assert (
        original.timeout_seconds == 300
        and original.cpus == 2
        and original.memory_mb == 4096
    )
    assert original.batch.prepare_swe


def test_gpu_is_not_silently_excluded_or_counted_as_failure(tmp_path, upstream):
    with pytest.raises(ValueError, match="需要 GPU/TPU"):
        prepare(tmp_path)
    assert not (tmp_path / "dataset").exists()
    with pytest.raises(ValueError, match="没有可运行"):
        prepare(tmp_path, names=["gpu-task"], cpu_only=True)


def test_selection_reproducible_and_filters_before_sampling(tmp_path, upstream):
    paths = []
    for name in ("first", "second"):
        root = tmp_path / name
        result = prepare(root, count=1, seed=42, cpu_only=True)
        paths.append(
            json.loads((Path(result["dataset"]) / "selection.json").read_text())[
                "tasks"
            ]
        )
    assert paths[0] == paths[1]
    assert len(paths[0]) == 1
    with pytest.raises(ValueError, match="CPU 可选"):
        prepare(tmp_path / "too-many", count=3, cpu_only=True)


def test_explicit_selection_and_no_overwrite(tmp_path, upstream):
    result = prepare(tmp_path, names=["beta", "alpha"])
    selection = json.loads((Path(result["dataset"]) / "selection.json").read_text())
    assert list(selection["tasks"]) == ["alpha", "beta"]
    before = tree_manifest(tmp_path / "dataset")
    with pytest.raises(FileExistsError):
        prepare(tmp_path, names=["alpha"])
    assert tree_manifest(tmp_path / "dataset") == before


@pytest.mark.parametrize(
    "reference",
    ["terminal-bench", "terminal-bench@latest", "terminal-bench@head", "other@2.0"],
)
def test_mutable_or_non_terminal_references_rejected(reference):
    with pytest.raises(ValueError, match="固定官方"):
        terminal.pinned_reference(reference)


def test_immutable_package_registry_supported(tmp_path, upstream):
    _, metadata = upstream
    metadata.name = "terminal-bench/terminal-bench"
    metadata.version = "sha256:" + "b" * 64
    metadata.task_ids = [
        PackageTaskId(org="terminal-bench", name="alpha", ref="sha256:" + "c" * 64)
    ]
    result = asyncio.run(
        terminal.prepare(
            "terminal-bench/terminal-bench@4.0.0", tmp_path / "dataset", template()
        )
    )
    exp = load(Path(result["config"]))
    assert result["full_dataset"] is True
    assert exp.benchmark.dataset.revision == "sha256:" + "b" * 64


def test_bad_inputs_never_publish_partial_dataset(tmp_path, upstream):
    source, metadata = upstream
    with pytest.raises(ValueError, match="不存在"):
        prepare(tmp_path, names=["missing"])
    metadata.task_ids[0] = metadata.task_ids[0].model_copy(
        update={"git_commit_id": "main"}
    )
    with pytest.raises(ValueError, match="Git commit"):
        prepare(tmp_path, names=["alpha"])
    metadata.task_ids[0] = metadata.task_ids[0].model_copy(
        update={"git_commit_id": "a" * 40}
    )
    (source / "alpha/tests/test.sh").unlink()
    with pytest.raises(ValueError, match="缺少 tests/test.sh"):
        prepare(tmp_path, names=["alpha"])
    assert not (tmp_path / "dataset").exists()


def test_separate_verifier_keeps_upstream_isolation_and_disables_guardian(
    tmp_path, upstream
):
    source, _ = upstream
    config = source / "alpha/task.toml"
    config.write_text(
        config.read_text() + "\n[verifier.environment]\ncpus = 2\nmemory_mb = 2048\n"
    )
    result = prepare(tmp_path, names=["alpha"])
    exp = load(Path(result["config"]))
    assert exp.guardian is exp.batch is None
    selection = json.loads((Path(result["dataset"]) / "selection.json").read_text())
    assert selection["execution_mode"] == "harbor-job-separate-verifier"
    assert tree_manifest(source / "alpha") == tree_manifest(
        Path(result["dataset"]) / "alpha"
    )


def test_gpu_required_only_by_verifier_is_also_rejected(tmp_path, upstream):
    source, _ = upstream
    config = source / "alpha/task.toml"
    config.write_text(config.read_text() + "\n[verifier.environment]\ngpus = 1\n")
    with pytest.raises(ValueError, match="需要 GPU/TPU"):
        prepare(tmp_path, names=["alpha"])


def test_network_policy_inherits_environment_and_respects_override(
    tmp_path, upstream, monkeypatch
):
    source, _ = upstream
    monkeypatch.setattr(
        "agent_harness_eval.search.search_hosts", lambda _: ["example.invalid"]
    )
    # No explicit agent.network_mode: Harbor inherits public from environment.
    result = prepare(tmp_path / "public", names=["alpha"])
    assert load(Path(result["config"])).benchmark.id == "terminal-bench"
    config = source / "alpha/task.toml"
    text = config.read_text()
    config.write_text(
        text.replace("[environment]", '[environment]\nnetwork_mode = "no-network"')
    )
    with pytest.raises(ValueError, match="web_search requires"):
        prepare(tmp_path / "blocked", names=["alpha"])
    config.write_text(
        config.read_text().replace(
            "[agent]",
            '[agent]\nnetwork_mode = "allowlist"\nallowed_hosts = ["example.invalid"]',
        )
    )
    assert prepare(tmp_path / "allowlist", names=["alpha"])["selected"] == 1


def test_task_limits_reach_harbor_job(tmp_path, upstream, monkeypatch):
    import agent_harness_eval.run as runner
    from agent_harness_eval.run import run_experiment

    result = prepare(tmp_path, names=["alpha"])
    exp = load(Path(result["config"]))
    exp.batch = exp.guardian = None
    jobs = []

    class Job:
        @staticmethod
        async def create(config):
            jobs.append(config)
            return Job()

        async def run(self):
            pass

    monkeypatch.setattr(runner, "Job", Job)
    asyncio.run(run_experiment(exp, tmp_path / "run"))
    assert jobs[0].environment.override_cpus is None
    assert jobs[0].environment.override_memory_mb is None
    assert jobs[0].agents[0].override_timeout_sec is None
    frozen = tmp_path / "run/tasks/alpha"
    assert tree_manifest(frozen) == tree_manifest(Path(result["dataset"]) / "alpha")
    assert (tmp_path / "run/selection.json").read_bytes() == (
        Path(result["dataset"]) / "selection.json"
    ).read_bytes()


def test_console_discovers_and_freezes_terminal_template(tmp_path, upstream):
    from agent_harness_eval.console import Console

    path = tmp_path / "experiments/terminal.json"
    result = prepare(tmp_path, names=["alpha"], config_output=path)
    console = Console(tmp_path)
    row = next(row for row in console.templates() if row["id"] == "terminal")
    assert row["available"] is True
    assert row["defaults"]["cpus"] is row["defaults"]["timeout_seconds"] is None
    queued = console.submit({"template": "terminal"})
    frozen = json.loads(
        (tmp_path / ".console/inputs" / queued["id"] / "config.json").read_text()
    )
    assert frozen["cpus"] is frozen["timeout_seconds"] is None
    assert (
        Path(frozen["benchmark"]["tasks"][0]).parent / "selection.json"
    ).read_bytes() == (Path(result["dataset"]) / "selection.json").read_bytes()
