"""Language requirements for newly generated RSI content."""

DEFAULT_OBJECTIVE = "分析已保存的测试证据，先诊断异常原因、区分事实与假设，再确定是否需要修复执行框架，并验证修改。"

DIAGNOSIS_LANGUAGE_INSTRUCTIONS = (
    "First diagnose the recorded failure from evidence; do not assume a cause from case metadata. "
    "After analysis, generate a concise Chinese topic describing the diagnosed issue. "
    "If the cause remains uncertain, explicitly say 原因未确定 in the topic. "
    "Do not use the task name or generic improvement instructions as the topic. "
    "Write topic, summary, hypotheses, changes and limitations in Chinese. "
    "Provide title and title_en as concise, single-line Chinese and English change titles, "
    "without a prefix; the platform adds 【RSI-generated pr】. "
    "Start summary with the concrete "
    "failure trigger, previous behavior and resulting behavior in a short paragraph. "
    "Write changes as Markdown bullets explaining what changed and why. "
    "Write limitations as Markdown bullets describing scope, uncertainties, untested paths "
    "and checks or model evaluations not performed. Do not invent commands, environments, "
    "test counts or improvements; distinguish observed facts from hypotheses. "
    "Do not include section headings or platform metadata in these fields; the platform "
    "renders Chinese first, then English, with changes, validation, scope and limitations. "
    "Also provide faithful English translations in summary_en, changes_en and "
    "limitations_en for the bilingual PR title and body. Keep evidence IDs, "
    "technical identifiers, paths and commands unchanged."
)
