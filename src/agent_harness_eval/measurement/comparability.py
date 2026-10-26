"""Read-model gates supplement the frozen condition comparisons."""


def differences(left, right):
    reasons = []
    if left["source"] != right["source"]:
        reasons.append("主评分来源不一致")
    if any(value["states"]["scoring"] != "complete" for value in (left, right)):
        reasons.append("主评分尚未完整，不能直接比较最终成绩")
    if any(value["states"]["evidence"] != "complete" for value in (left, right)):
        reasons.append("原始执行证据不完整")
    if any(value["issues"] or value["unexpected"] for value in (left, right)):
        reasons.append("存在评分证据问题或计划外尝试")
    if any(agent["measurement_mode"] == "assisted" for value in (left, right) for agent in value["agents"].values()):
        reasons.append("含辅助尝试，不能混入纯自主对比")
    return reasons
