"""Language requirements for newly generated RSI content."""

DEFAULT_OBJECTIVE = "分析已保存的测试证据，先诊断异常原因、区分事实与假设，再确定是否需要修复执行框架，并验证修改。"

DIAGNOSIS_LANGUAGE_INSTRUCTIONS = (
    "First diagnose the recorded failure from evidence; do not assume a cause from case metadata. "
    "After analysis, generate a concise Chinese topic describing the diagnosed issue. "
    "If the cause remains uncertain, explicitly say 原因未确定 in the topic. "
    "Do not use the task name or generic improvement instructions as the topic. "
    "Write topic, summary, hypotheses, changes and limitations in Chinese. "
    "Also provide faithful English translations in summary_en, changes_en and "
    "limitations_en for the bilingual PR title and body. Keep evidence IDs, "
    "technical identifiers, paths and commands unchanged."
)
