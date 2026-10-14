"""Local experiment queue; Harbor remains the execution engine."""

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from .config import Experiment, Strict, load
from .manifests import load_manifest
from .provenance import validate_tree, write_json
from .report import duration, summarize, task_key


def now():
    return datetime.now(UTC).isoformat()


class Submission(Strict):
    template: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    name: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=80
    )
    agents: list[str] | None = Field(default=None, min_length=1)
    tasks: list[str] | None = Field(default=None, min_length=1)
    attempts: int | None = Field(default=None, ge=1, le=100)
    concurrency: int | None = Field(default=None, ge=1, le=16)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86400)
    cpus: int | None = Field(default=None, ge=1, le=64)
    memory_mb: int | None = Field(default=None, ge=256, le=131072)
    confirm_unbounded: bool = False
    model_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    guardian_model_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def safe_file(root, path):
    root, path = Path(root).resolve(), Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("文件路径越界或为符号链接")
    return path


def read_json(root, path, *, max_bytes=16 * 1024 * 1024):
    try:
        path = safe_file(root, path)
        if path.stat().st_size > max_bytes:
            return None
        data = json.loads(
            path.read_text(),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_manifest(root):
    try:
        safe_file(root, root / "manifest.json")
        return load_manifest(root)
    except (OSError, ValueError, TypeError):
        return None


def usable_report(value):
    """Reject broken result shapes before they reach the browser tables."""
    if (
        not value
        or not isinstance(value.get("agents"), dict)
        or not isinstance(value.get("trials"), list)
    ):
        return False
    for summary in value["agents"].values():
        if not isinstance(summary, dict):
            return False
        for key in ("expected", "recorded", "missing", "errors", "scored"):
            if not isinstance(summary.get(key), int) or summary[key] < 0:
                return False
        for key in ("mean_on_scored", "cost_usd"):
            if summary.get(key) is not None and not isinstance(
                summary[key], (int, float)
            ):
                return False
    return all(
        isinstance(t, dict) and isinstance(t.get("agent"), str) for t in value["trials"]
    )


def redact(text):
    for key, value in os.environ.items():
        if (
            re.search(r"KEY|TOKEN|SECRET|PASSWORD", key, re.IGNORECASE)
            and len(value) >= 8
        ):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r'(?i)Bearer\s+[^\s"\x27]+', "Bearer [REDACTED]", text)
    return re.sub(
        r"(?:sk-[\w-]{20,}|gh[pousr]_[\w]{25,}|github_pat_[\w]{30,})",
        "[REDACTED]",
        text,
    )


class Console:
    def __init__(self, root: Path, allow_live=False):
        self.root = root.resolve()
        self.state = self.root / ".console"
        self.artifacts = self.root / "artifacts"
        self.allow_live = allow_live
        self.state.mkdir(exist_ok=True)
        self.artifacts.mkdir(exist_ok=True)
        for folder in ("inputs", "logs"):
            (self.state / folder).mkdir(exist_ok=True)
        self.db = self.state / "runs.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, config TEXT NOT NULL,
                status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
                historical INTEGER NOT NULL DEFAULT 0, error TEXT, pid INTEGER)""")
        self.stop = threading.Event()
        self.worker = None
        self.process = None
        self.lock = None
        self.blocked = None
        from .model_pool import ModelPool

        self.models = ModelPool(self.root)
        self.models.migrate()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def templates(self):
        rows = []
        for path in sorted((self.root / "experiments").glob("*.json")):
            data = read_json(self.root / "experiments", path)
            if not data:
                continue
            try:
                exp = Experiment.model_validate(data)
            except ValueError:
                continue
            error = None
            try:
                load(path)
            except (ValueError, OSError) as exc:
                error = str(exc)
            if exp.model.mode == "live" and not self.allow_live:
                error = "真实模型执行未开启：启动服务时需指定 --allow-live"
            elif exp.model.mode == "live" and not os.environ.get(exp.model.api_key_env):
                try:
                    self.models.resolve("experiment")
                except ValueError:
                    if not any(
                        m["has_api_key"] for m in self.models.public()["models"]
                    ):
                        error = "请在模型设置中添加实验 Agent 模型"
            rows.append(
                {
                    "id": path.stem,
                    "name": exp.name,
                    "mode": exp.model.mode,
                    "model": exp.model.name,
                    "guardian_mode": exp.guardian.mode if exp.guardian else None,
                    "agents": [a.id for a in exp.agents],
                    "tasks": [p.name for p in exp.benchmark.tasks],
                    "benchmark": exp.benchmark.id,
                    "dataset": exp.benchmark.dataset.model_dump()
                    if exp.benchmark.dataset
                    else None,
                    "available": error is None,
                    "error": error,
                    "defaults": {
                        k: getattr(exp, k)
                        for k in (
                            "attempts",
                            "concurrency",
                            "timeout_seconds",
                            "cpus",
                            "memory_mb",
                        )
                    },
                }
            )
        return rows

    def submit(self, data):
        request = Submission.model_validate(data)
        if self.blocked:
            raise ValueError(self.blocked)
        path = safe_file(
            self.root / "experiments",
            self.root / "experiments" / (request.template + ".json"),
        )
        exp = load(path)
        if exp.model.mode != "live" and request.model_id:
            raise ValueError("离线和模拟实验不能切换为真实模型")
        if exp.model.mode == "live" and (
            request.model_id or self.models.public()["bindings"].get("experiment")
        ):
            exp.model = type(exp.model).model_validate(
                self.models.resolve("experiment", request.model_id)
            )
        if request.guardian_model_id and (
            not exp.guardian or exp.guardian.mode != "assist"
        ):
            raise ValueError("仅自主介入模式使用守护模型；模型选择不会自动启用介入")
        if (
            exp.guardian
            and exp.guardian.mode == "assist"
            and (
                request.guardian_model_id
                or self.models.public()["bindings"].get("guardian")
            )
        ):
            exp.guardian.model = self.models.resolve(
                "guardian", request.guardian_model_id
            )
            exp.guardian.command = []
            exp.guardian.version = "model-pool-driver/v2"
        if (
            exp.guardian
            and exp.guardian.model
            and exp.guardian.mode == "assist"
            and (not self.allow_live or not request.confirm_unbounded)
        ):
            raise ValueError("守护模型运行需要开启 --allow-live 并确认费用")
        if exp.model.mode == "live":
            if not self.allow_live or not request.confirm_unbounded:
                raise ValueError(
                    "真实运行需要服务开启 --allow-live，并明确确认不设费用上限"
                )
            if not (
                self.models.environment(exp.model.model_dump()).get(
                    exp.model.api_key_env
                )
                or os.environ.get(exp.model.api_key_env)
            ):
                raise ValueError("服务环境中缺少模型凭据")
        config = exp.model_dump(mode="json")
        for field in (
            "name",
            "attempts",
            "concurrency",
            "timeout_seconds",
            "cpus",
            "memory_mb",
        ):
            if getattr(request, field) is not None:
                config[field] = getattr(request, field)
        for field, choices, key in [
            ("agents", config["agents"], lambda a: a["id"]),
            ("tasks", config["benchmark"]["tasks"], lambda t: Path(t).name),
        ]:
            selected = getattr(request, field)
            if selected is not None:
                if len(selected) != len(set(selected)) or not set(selected) <= {
                    key(c) for c in choices
                }:
                    raise ValueError(f"{field} 必须是模板中的不重复选项")
                values = [c for c in choices if key(c) in selected]
                if field == "agents":
                    config[field] = values
                else:
                    config["benchmark"]["tasks"] = values
        Experiment.model_validate(config)
        run_id = uuid.uuid4().hex
        frozen = self.state / "inputs" / run_id
        frozen.mkdir()
        try:
            for i, task in enumerate(config["benchmark"]["tasks"]):
                source = Path(task)
                validate_tree(
                    source
                )  # Reject symlinks before copying executable inputs.
                target = frozen / "tasks" / source.name
                from .freezing import freeze_tree

                freeze_tree(source, target, frozen / "shared-runtime")
                config["benchmark"]["tasks"][i] = str(target)
            for agent in config["agents"]:
                for key in ("binary", "bundle", "provenance"):
                    if key in agent["kwargs"]:
                        source = Path(agent["kwargs"][key])
                        target = frozen / "agents" / agent["id"] / source.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
                        agent["kwargs"][key] = str(target)
            write_json(frozen / "config.json", config)
            with self.connect() as db:
                stamp = now()
                db.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?,?,0,NULL,NULL)",
                    (
                        run_id,
                        str(self.artifacts / ("web-" + run_id)),
                        json.dumps(config),
                        "queued",
                        stamp,
                        stamp,
                    ),
                )
        except Exception:
            shutil.rmtree(frozen)
            raise
        return self.detail(run_id)

    def import_history(self):
        with self.connect() as db:
            for path in sorted(self.artifacts.iterdir()):
                if not path.is_dir() or path.is_symlink():
                    continue
                manifest = read_manifest(path)
                if not manifest or not isinstance(manifest.get("experiment"), dict):
                    continue
                try:
                    config = Experiment.model_validate(
                        manifest["experiment"]
                    ).model_dump(mode="json")
                except ValueError:
                    continue
                identity = (
                    "history-" + hashlib.sha256(path.name.encode()).hexdigest()[:20]
                )
                stamp = manifest.get("created_at") or now()
                db.execute(
                    "INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?,?,1,NULL,NULL)",
                    (
                        identity,
                        str(path),
                        json.dumps(config),
                        "incomplete",
                        stamp,
                        stamp,
                    ),
                )

    def row(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("找不到运行")
        return dict(row)

    def evidence(self, row):
        from .guardian.evidence import incident_summary

        path = safe_file(self.artifacts, Path(row["path"]))
        manifest = read_manifest(path)
        report = read_json(path, path / "report.json")
        if not usable_report(report):
            report = None
        completed = 0
        trials = []
        for result in sorted(path.glob("jobs/*/*/result.json")):
            value = read_json(path, result)
            if value and value.get("finished_at") and value.get("task_name"):
                completed += 1
                verifier = value.get("verifier_result") or {}
                if not isinstance(verifier, dict):
                    verifier = {}
                metrics = verifier.get("rewards") or {}
                if not isinstance(metrics, dict) or any(
                    not isinstance(v, (int, float)) or not math.isfinite(v)
                    for v in metrics.values()
                ):
                    metrics = {}
                usage = value.get("agent_result") or {}
                if not isinstance(usage, dict):
                    usage = {}
                trials.append(
                    {
                        "id": hashlib.sha256(
                            str(result.relative_to(path)).encode()
                        ).hexdigest()[:20],
                        "trial": result.parent.name,
                        "agent": result.parent.parent.name,
                        "task": task_key(value),
                        "exception": value.get("exception_info"),
                        "metrics": metrics,
                        "agent_seconds": duration(value.get("agent_execution") or {}),
                        "input_tokens": usage.get("n_input_tokens"),
                        "output_tokens": usage.get("n_output_tokens"),
                        "cost_usd": usage.get("cost_usd"),
                        "guardian": incident_summary(result.parent),
                    }
                )
        return manifest, report, completed, trials

    @staticmethod
    def report_state(report, expected):
        if not report or report.get("report_errors"):
            return "incomplete"
        agents = report.get("agents", {})
        try:
            if sum(a["recorded"] for a in agents.values()) != expected or any(
                a["missing"] for a in agents.values()
            ):
                return "incomplete"
            if any(
                a["errors"] or a["scored"] != a["expected"] for a in agents.values()
            ):
                return "failed"
        except (KeyError, TypeError):
            return "incomplete"
        return "completed"

    def detail(self, run_id):
        row = self.row(run_id)
        exp = json.loads(row["config"])
        expected = len(exp["agents"]) * len(exp["benchmark"]["tasks"]) * exp["attempts"]
        manifest, report, completed, trials = self.evidence(row)
        if row["historical"]:
            row["status"] = self.report_state(report, expected)
        batch_state = read_json(
            Path(row["path"]), Path(row["path"]) / "batch-state.json"
        )
        if (
            row["historical"]
            and batch_state
            and batch_state.get("phase")
            in {"preparing", "running", "regrading", "cleaning"}
        ):
            runner = batch_state.get("runner", {})
            try:
                process = Path("/proc") / str(int(runner["pid"]))
                start = (process / "stat").read_text().rsplit(")", 1)[1].split()[19]
                command = (process / "cmdline").read_bytes().decode(
                    errors="replace"
                ).split("\0")
                output = None
                for index, argument in enumerate(command):
                    if argument == "--output":
                        output = command[index + 1]
                    elif argument.startswith("--output="):
                        output = argument.removeprefix("--output=")
                # Shared artifacts can be reached through a console's symlink.
                # Compare the actual output directory, not a cmdline substring.
                output_path = Path(output) if output else None
                if output_path is not None and not output_path.is_absolute():
                    output_path = (process / "cwd").resolve() / output_path
                row["status"] = (
                    "running"
                    if start == runner["start_ticks"]
                    and output_path is not None
                    and output_path.resolve() == Path(row["path"]).resolve()
                    else "interrupted"
                )
            except (OSError, ValueError, KeyError, IndexError):
                row["status"] = "interrupted"
        from .trace_view import decorate

        if row["status"] == "running":
            report = summarize(
                {
                    "experiment": exp,
                    "tasks": (manifest or {}).get("tasks", {}),
                    "evidence_kind": (manifest or {}).get("evidence_kind", ""),
                },
                trials,
            )
            report["live"] = True

        summaries = (report or {}).get("agents", {}).values()
        execution_summary = {
            "finished": (
                row["status"] in {"completed", "failed"}
                and completed == expected
                and self.report_state(report, expected) in {"completed", "failed"}
            ),
            "errors": sum(a["errors"] for a in summaries) if report else None,
            "unscored": (
                max(0, expected - sum(a["scored"] for a in summaries))
                if report else None
            ),
        }

        return decorate(
            self.root,
            {
                "id": row["id"],
                "name": exp["name"],
                "status": row["status"],
                "created": row["created"],
                "updated": row["updated"],
                "historical": bool(row["historical"]),
                "directory": Path(row["path"]).name,
                "mode": exp["model"]["mode"],
                "benchmark": exp["benchmark"]["id"],
                "dataset": exp["benchmark"].get("dataset"),
                "agents": [a["id"] for a in exp["agents"]],
                "expected": expected,
                "completed": completed,
                "execution_summary": execution_summary,
                "error": row["error"],
                "experiment": exp,
                "batch_state": batch_state,
                "report": report,
                "trials": trials,
                "task_hashes": {
                    k: v.get("sha256")
                    for k, v in (manifest or {}).get("tasks", {}).items()
                    if isinstance(v, dict)
                },
                "grading": "Harbor 内置评分；不代表官方 SWE 独立复评",
                "trajectory_note": "ATIF 为末次上下文与终态视图，原始事件为完整诊断依据",
            },
        )

    def runs(self):
        self.import_history()
        with self.connect() as db:
            ids = [
                r[0] for r in db.execute("SELECT id FROM runs ORDER BY created DESC")
            ]
        result = []
        for run_id in ids:
            try:
                row = self.detail(run_id)
                result.append(
                    {
                        k: v
                        for k, v in row.items()
                        if k not in ("experiment", "report", "task_hashes", "trials")
                    }
                )
            except (ValueError, OSError):
                continue
        return result

    def logs(self, run_id):
        row = self.row(run_id)
        if row["historical"]:
            return (
                "历史运行未保存控制台执行日志。原始证据目录：artifacts/"
                + Path(row["path"]).name
            )
        path = safe_file(self.state, self.state / "logs" / (run_id + ".log"))
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 32768))
                return (
                    self.models.redact(
                        redact(stream.read(32768).decode(errors="replace"))
                    )
                    or "评测进程正在运行，尚无标准输出。任务进度以已落盘结果为准。"
                )
        except FileNotFoundError:
            return "等待执行日志…"

    def comparison(self, run_id):
        from .comparison import summarize

        detail = self.detail(run_id)
        benchmark = detail["experiment"]["benchmark"]
        root = safe_file(self.artifacts, Path(self.row(run_id)["path"]))
        task_names = [Path(t).name for t in benchmark["tasks"]]
        # Historical configs predate score_kind. Only frozen SWE evidence identifies them.
        provenance = [
            read_json(root, root / "tasks" / t / "swe-provenance.json")
            for t in task_names
        ]
        kind = benchmark.get("score_kind", "metric")
        original = (
            (read_manifest(root) or {}).get("experiment", {}).get("benchmark", {})
        )
        if (
            "score_kind" not in original
            and benchmark["primary_metric"] == "reward"
            and all(p and p.get("instance_id") for p in provenance)
        ):
            kind = "resolved"
        data = summarize(
            detail["experiment"],
            detail["trials"],
            kind,
            detail["status"] in ("completed", "failed", "incomplete", "interrupted"),
        )
        data.update(
            run={
                k: detail[k]
                for k in ("id", "name", "status", "mode", "grading", "reviews")
            },
            benchmark={
                "id": benchmark["id"],
                "dataset": benchmark.get("dataset"),
                "evaluation": benchmark.get("evaluation"),
                "task_hashes": detail["task_hashes"],
            },
        )
        return data

    def compare(self, left, right):
        a, b = self.detail(left), self.detail(right)
        reasons = []
        for field, label in [
            ("dataset", "数据集版本 / split"),
            ("evaluation", "评分器 / 环境版本"),
            ("score_kind", "评分口径"),
        ]:
            va, vb = (r["experiment"]["benchmark"].get(field) for r in (a, b))
            if va != vb:
                reasons.append(label + " 不一致")
        datasets = [r["experiment"]["benchmark"].get("dataset") for r in (a, b)]
        if any(d and (not d.get("revision") or not d.get("split")) for d in datasets):
            reasons.append("数据集修订或 split 未记录")
        elif any(not d for d in datasets) and any(
            read_json(
                self.artifacts,
                self.artifacts
                / r["directory"]
                / "tasks"
                / Path(t).name
                / "swe-provenance.json",
            )
            for r in (a, b)
            for t in r["experiment"]["benchmark"]["tasks"]
        ):
            reasons.append("历史 SWE 数据集名称 / 修订 / split 未记录")
        for field, label in [
            ("benchmark", "Benchmark"),
            ("task_hashes", "任务内容哈希"),
        ]:
            if not a[field] or a[field] != b[field]:
                reasons.append(label + " 不一致或缺失")
        for field, label in [
            ("model", "模型配置"),
            ("attempts", "重复次数"),
            ("cpus", "CPU"),
            ("memory_mb", "内存"),
            ("timeout_seconds", "超时"),
            ("concurrency", "并发"),
            ("setup_timeout_seconds", "准备超时"),
        ]:
            if a["experiment"][field] != b["experiment"][field]:
                reasons.append(label + " 不一致")
        if a["experiment"].get("guardian") != b["experiment"].get("guardian"):
            reasons.append("实验守护策略不一致")
        if any(
            t.get("guardian", {}).get("assisted") for r in (a, b) for t in r["trials"]
        ):
            reasons.append("存在外部辅助尝试，不能作为纯自主成绩直接配对")
        if (
            a["experiment"]["benchmark"]["primary_metric"]
            != b["experiment"]["benchmark"]["primary_metric"]
        ):
            reasons.append("主指标不一致")
        if not a["report"] or not b["report"]:
            reasons.append("缺少结果报告")
        if a["status"] != "completed" or b["status"] != "completed":
            reasons.append("存在未完成或异常运行，不能直接配对")
        return {
            "left": a,
            "right": b,
            "compatible": not reasons,
            "differences": reasons,
        }

    def update(self, run_id, status, error=None, pid=None):
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET status=?,error=?,pid=?,updated=? WHERE id=?",
                (status, error, pid, now(), run_id),
            )

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM runs WHERE status='running' AND historical=0"
            ).fetchone():
                return None
            row = db.execute(
                "SELECT * FROM runs WHERE status='queued' ORDER BY created LIMIT 1"
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE runs SET status='running',updated=? WHERE id=?",
                    (now(), row["id"]),
                )
                return dict(row)
        return None

    def start(self):
        self.lock = (self.state / "server.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            raise RuntimeError("该项目已有控制台服务，或上次评测进程尚未退出") from None
        with self.connect() as db:
            abandoned = db.execute(
                "SELECT id FROM runs WHERE status='running' AND historical=0"
            ).fetchall()
        for row in abandoned:
            self.update(
                row["id"],
                "interrupted",
                "服务意外退出；检查遗留进程和 Docker 容器后运行 recover",
            )
        with self.connect() as db:
            interrupted = db.execute(
                "SELECT 1 FROM runs WHERE status='interrupted' AND historical=0"
            ).fetchone()
        if interrupted:
            self.blocked = "存在中断运行。请检查遗留进程和 Docker 容器，处理后使用 agent-harness-eval serve --recover 启动"
        self.import_history()
        from .rsi.store import Store

        Store(self.root).recover()
        self.worker = threading.Thread(
            target=self.work, daemon=True, name="evaluation-worker"
        )
        self.worker.start()

    def work(self):
        while not self.stop.is_set():
            if self.blocked:
                self.stop.wait(1)
                continue
            row = self.claim()
            if row is None:
                from .rsi.store import Store

                action = Store(self.root).next_action()
                if action:
                    self.execute_rsi(action)
                    continue
                self.stop.wait(0.5)
                continue
            try:
                self.execute(row)
            except Exception as exc:  # noqa: BLE001 - persist failures at the worker boundary
                if self.process is not None:
                    self.terminate()
                    self.process = None
                    self.blocked = (
                        "执行管理异常，检查遗留 Docker 容器后使用 --recover 重启"
                    )
                    self.update(row["id"], "interrupted", redact(str(exc)))
                else:
                    self.update(row["id"], "failed", redact(str(exc)))

    def execute(self, row):
        run_id = row["id"]
        if self.stop.is_set():
            self.update(run_id, "queued")
            return
        config = self.state / "inputs" / run_id / "config.json"
        model = json.loads(row["config"])["model"]
        guardian = json.loads(row["config"]).get("guardian")
        child_env = {
            **os.environ,
            **self.models.environment(model, (guardian or {}).get("model")),
        }
        # Allow the independent supervisor to persist evidence and clean Docker.
        self.shutdown_seconds = (
            4 * guardian["command_seconds"] + guardian["cleanup_seconds"] + 30
            if guardian
            else 8
        )
        if model["mode"] == "live" and (
            not self.allow_live or not child_env.get(model["api_key_env"])
        ):
            raise ValueError(
                "当前服务未开启真实模型执行或缺少凭据；旧队列不绕过本次启动策略"
            )
        if (
            guardian
            and guardian.get("model")
            and guardian["mode"] == "assist"
            and not self.allow_live
        ):
            raise ValueError("当前服务未开启守护模型执行")
        with (self.state / "logs" / (run_id + ".log")).open("wb") as log:
            # Inherited flock prevents a restarted server launching alongside an orphan CLI.
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "agent_harness_eval.cli",
                    "run",
                    str(config),
                    "--output",
                    row["path"],
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=self.root,
                env=child_env,
                start_new_session=True,
                pass_fds=(self.lock.fileno(),),
            )
            self.update(run_id, "running", pid=self.process.pid)
            while self.process.poll() is None:
                if self.stop.wait(0.3):
                    self.terminate()
                    break
            code = self.process.wait()
        self.process = None
        if self.stop.is_set():
            self.update(
                run_id, "interrupted", "服务已关闭；检查 Docker 清理情况后恢复队列"
            )
        elif code:
            self.update(run_id, "failed", f"评测进程退出码 {code}；请查看执行日志")
        else:
            detail = self.detail(run_id)
            status = self.report_state(detail["report"], detail["expected"])
            self.update(
                run_id,
                "completed" if status == "completed" else "failed",
                None if status == "completed" else "执行已结束，但有异常或结果不完整",
            )

    def execute_rsi(self, action):
        from .rsi.store import Store

        store = Store(self.root)
        self.shutdown_seconds = 150
        argv = [
            sys.executable,
            "-m",
            "agent_harness_eval.cli",
            "rsi-work",
            action["id"],
            "--root",
            str(self.root),
        ]
        if self.allow_live:
            argv.append("--allow-live")
        try:
            with (store.root / "actions" / (action["id"] + ".log")).open("wb") as log:
                self.process = subprocess.Popen(
                    argv,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    cwd=self.root,
                    start_new_session=True,
                    pass_fds=(self.lock.fileno(),),
                )
                while self.process.poll() is None:
                    if self.stop.wait(0.3):
                        self.terminate()
                        break
                code = self.process.wait()
            if store.action(action["id"])["status"] == "queued":
                store.finish(
                    action["id"],
                    "failed",
                    f"Worker exited before claiming action ({code})",
                )
            # A simultaneous standalone CLI may have claimed this action. Its
            # candidate lock must be checked before marking anything interrupted.
            store.recover()
        except Exception as error:  # noqa: BLE001 - protect the shared queue worker
            if self.process:
                self.terminate()
            if store.action(action["id"])["status"] == "queued":
                store.finish(action["id"], "failed", redact(str(error)))
            store.recover()
        finally:
            self.process = None

    def terminate(self):
        process = self.process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=getattr(self, "shutdown_seconds", 8))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join(timeout=getattr(self, "shutdown_seconds", 8) + 12)
        if self.lock:
            self.lock.close()
            self.lock = None
