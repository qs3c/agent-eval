import pytest

from agent_eval.dsh_bundle import audit_standard


def test_standard_audit_rejects_missing_compaction(tmp_path):
    modules = [
        "persona",
        "tool-bash",
        "tool-fs",
        "agent-instructions",
        "compaction-basic",
        "compaction-tool-result-pruner",
        "tool-subagent",
        "tool-todo",
    ]
    p = tmp_path / "preset.yml"

    def write(modules):
        import yaml

        p.write_text(
            yaml.safe_dump(
                [
                    {
                        "insert": [
                            {
                                "id": "preset-standard",
                                "config": {
                                    "id": "standard",
                                    "plugins": [
                                        {"name": "@deepseek-ai/dsh-" + m}
                                        for m in modules
                                    ],
                                },
                            }
                        ]
                    }
                ]
            )
        )

    write(modules)
    assert audit_standard(p)["config_audit"] == "passed"
    write([m for m in modules if m != "compaction-basic"])
    with pytest.raises(ValueError, match="compaction-basic"):
        audit_standard(p)
