"""Read-only recovery of partial main-model usage from retained wire evidence."""

import json
from functools import lru_cache

from .traces import summarize_usage


@lru_cache(maxsize=512)
def _read_usage(files):
    rows = []
    issues = []
    for filename, _size, _mtime in files:
        records = []
        try:
            with open(filename) as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError()
                        if row.get("request_id") is not None and not isinstance(row["request_id"], str):
                            raise ValueError()
                        if row.get("usage") is not None and not isinstance(row["usage"], dict):
                            raise ValueError()
                        if "schema_version" in row and type(row["schema_version"]) is not int:
                            raise ValueError()
                        if "purpose" in row and not isinstance(row["purpose"], str):
                            raise ValueError()
                        # Do not cache prompts or tool contents.
                        records.append({k: row[k] for k in (
                            "request_id", "purpose", "usage", "schema_version",
                            "outcome", "stream_complete", "status") if k in row})
                    except (ValueError, TypeError):
                        issues.append("部分日志无法解析")
        except OSError:
            issues.append("日志缺失或不可读取")
        rows.append(records)
    _, coverage = summarize_usage(*rows)
    main = dict(coverage["usage_by_purpose"]["agent"])
    if issues:
        main["usage_complete"] = False
        main["usage_issues"] = {**main["usage_issues"], **{i: 1 for i in issues}}
    return main


def trial_usage(path, result_usage):
    """Keep strict recorded totals intact; recover a separately labelled subtotal."""
    if all(type(result_usage.get("n_" + k)) is int and result_usage["n_" + k] >= 0
           for k in ("input_tokens", "output_tokens")):
        return {"usage_complete": True,
                "known_usage": {k: result_usage["n_" + k] for k in ("input_tokens", "output_tokens")},
                "missing_usage_requests": 0, "usage_issues": {}}
    files = []
    for name in ("native_wire_requests.jsonl", "wire_responses.jsonl"):
        file = path / "agent" / name
        try:
            if not file.resolve().is_relative_to(path.resolve()):
                raise OSError("outside trial")
            stat = file.stat()
            if stat.st_size > 256 * 1024 * 1024:
                raise OSError("oversize evidence")
            files.append((str(file), stat.st_size, stat.st_mtime_ns))
        except OSError:
            return {"usage_complete": False, "known_usage": {},
                    "missing_usage_requests": None,
                    "usage_issues": {"日志缺失、过大或不可读取": 1}}
    return _read_usage(tuple(files))
