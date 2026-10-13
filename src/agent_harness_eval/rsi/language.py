"""Chinese presentation of legacy objectives without rewriting frozen evidence."""

DEFAULT_OBJECTIVE = "根据已记录的证据诊断问题并改进执行框架"

LEGACY_OBJECTIVES = {
    "Diagnose and improve the harness using the recorded evidence": DEFAULT_OBJECTIVE,
    "Diagnose the shell timeout and retained process pipeline using original trace and separately labelled human-intervention snapshots.": "根据原始执行记录及单独标注的人工介入快照，诊断 shell 超时及进程管道残留问题。",
    "Diagnose the recorded API protocol failure; distinguish evidence from hypotheses before changing the harness.": "诊断已记录的 API 协议故障；修改执行框架前，先区分证据与假设。",
}


def chinese_objective(objective):
    return LEGACY_OBJECTIVES.get(objective, objective)


DIAGNOSIS_LANGUAGE_INSTRUCTIONS = (
    "Write summary, hypotheses, changes and limitations in Chinese. "
    "Also provide faithful English translations in summary_en, changes_en and "
    "limitations_en for the bilingual PR title and body. Keep evidence IDs, "
    "technical identifiers, paths and commands unchanged."
)
