"""Language requirements for newly generated RSI content."""

DEFAULT_OBJECTIVE = "根据已记录的证据诊断问题并改进执行框架"

DIAGNOSIS_LANGUAGE_INSTRUCTIONS = (
    "Write summary, hypotheses, changes and limitations in Chinese. "
    "Also provide faithful English translations in summary_en, changes_en and "
    "limitations_en for the bilingual PR title and body. Keep evidence IDs, "
    "technical identifiers, paths and commands unchanged."
)
