"use strict";
const reviewNames = {
  accepted: "已复核有效",
  diagnostic: "仅供诊断",
  unreviewed: "待复核",
};
function fold(title, text) {
  const d = el("details"),
    s = el("summary", title);
  d.append(s, el("pre", text));
  return d;
}
function traceCard(t) {
  const card = el("section", undefined, "panel trace-card");
  const tokens = key => tokenUsageLabel(t.metrics[key], {
    known: t.usage_evidence?.known_usage?.[key],
    missing_requests: t.usage_evidence?.missing_usage_requests,
    issues: Object.keys(t.usage_evidence?.usage_issues || {}),
  });
  card.append(
    el("h2", t.agent),
    el("p", t.trial, "mono"),
    el(
      "div",
      `${reviewNames[t.review.status]} · ${t.review.reason}`,
      "callout",
    ),
  );
  card.append(
    table(
      ["观测指标", "值"],
      [
        ["Agent 执行秒数（含运行器边界）", number(t.metrics.agent_seconds)],
        ["模型请求", number(t.metrics.requests)],
        ["工具调用", number(t.metrics.tools)],
        ["多工具轮次（不等同于并行）", number(t.metrics.multi_tool_rounds)],
        ["输入 token（含缓存）", tokens("input_tokens")],
        ["缓存命中输入", number(t.metrics.cached_tokens)],
        ["未命中缓存输入", number(t.metrics.uncached_tokens)],
        ["输出 token", tokens("output_tokens")],
        ["工具返回中的错误信号", number(t.metrics.error_signals)],
        [
          "工具分布",
          Object.entries(t.tool_counts)
            .map(([k, v]) => `${k}: ${v}`)
            .join(" / "),
        ],
        ["补丁文件", t.patch_files.join("、") || "未记录"],
        ["内置验证器结果", JSON.stringify(t.rewards) || "未知"],
      ],
    ),
  );
  if (t.review.findings?.length) card.append(el("h3", "已复核的历史样本分析"));
  for (const finding of t.review.findings || []) {
    const block = el("div", undefined, "callout warning");
    block.append(el("h3", finding.title), el("p", finding.text));
    for (const source of finding.sources) {
      const button = el("button", source, "button evidence-link");
      button.type = "button";
      button.onclick = () => {
        const rounds = card.querySelectorAll(".trace-round");
        const line = Number(source.split(":")[1]);
        let index = t.rounds.findIndex((r) =>
          r.tools.some(
            (x) => x.source === source || x.result_source === source,
          ),
        );
        if (index < 0)
          index = t.rounds.findLastIndex(
            (r) => Number(r.source.split(":")[1]) <= line,
          );
        if (index >= 0) {
          const target = rounds[index];
          target.open = true;
          target.querySelectorAll("details").forEach((d) => (d.open = true));
          target.scrollIntoView({ block: "center" });
        }
      };
      block.append(button);
    }
    card.append(block);
  }
  t.warnings.forEach((w) => card.append(el("p", w, "callout warning")));
  card.append(
    fold("数据口径与限制", t.limitations.join("\n")),
    fold("查看最终补丁", t.patch || "未保存补丁"),
  );
  card.append(
    el("h3", "逐轮执行证据"),
    el(
      "p",
      "优先通过 tool_call_id 关联模型响应与工具事件；降级关联会单独标注。点击展开原文；来源相对于证据目录。",
    ),
  );
  for (const r of t.rounds) {
    const d = el("details", undefined, "trace-round");
    d.append(
      el(
        "summary",
        `第 ${r.index} 轮 · ${r.tools.map((x) => x.name).join(" → ") || "最终回复"} · 输入 ${number(r.input_tokens)} / 输出 ${number(r.output_tokens)}`,
      ),
    );
    d.append(
      el(
        "p",
        `${r.source} · ${r.request_source || "请求无法可靠关联"}`,
        "mono",
      ),
    );
    d.append(
      el(
        "p",
        `缓存命中 ${number(r.cached_tokens)} · request_id ${r.request_id || "未知"}`,
      ),
    );
    d.append(el("p", "轮次开始事件：" + (r.started_at || "未记录"), "mono"));
    d.append(
      el(
        "p",
        "关联依据：" +
          ({
            tool_call_id: "工具 ID 精确关联",
            sequence_only: "仅按日志顺序（非精确关联）",
            unknown: "证据不足，未关联",
          }[r.association] || "旧版顺序关联"),
      ),
    );
    if (r.timing_ms) {
      const m = r.timing_ms;
      const delta = (end, start) =>
        typeof m[end] === "number" && typeof m[start] === "number"
          ? m[end] - m[start]
          : null;
      d.append(
        table(
          ["代理观测阶段", "毫秒"],
          [
            ["转发开始 → 响应头", number(delta("headers", "upstream_start"))],
            [
              "转发开始 → 首事件",
              number(delta("first_event", "upstream_start")),
            ],
            [
              "转发开始 → 首输出增量",
              number(delta("first_output_delta", "upstream_start")),
            ],
            ["请求处理总窗口", number(delta("finished", "received"))],
          ],
        ),
        el(
          "p",
          `结束状态：${r.outcome} · 流完整结束：${r.response_complete === true ? "是" : "否"}。以上为代理观测时间，包含网络、服务端等待、转发及日志开销；不是纯推理耗时。首输出可能是推理或工具参数。`,
        ),
      );
    } else
      d.append(
        el("p", "此历史请求没有代理阶段计时，不能补算首输出延迟。", "muted"),
      );
    if (r.request)
      d.append(
        fold("请求摘要（非完整上下文）", JSON.stringify(r.request, null, 2)),
      );
    for (const tool of r.tools) {
      const block = el("div", undefined, "trace-tool");
      block.append(
        el(
          "h4",
          tool.name +
            (tool.error_signal ? " · 错误信号，需核对" : "") +
            (tool.repeated ? " · 相同参数重复调用" : ""),
        ),
        fold("参数 · " + tool.source, tool.arguments),
        fold("返回 · " + (tool.result_source || "缺失"), tool.result),
      );
      block.append(
        el(
          "p",
          `调用事件：${tool.started_at || "未知"}；返回事件：${tool.returned_at || "未知"}（日志时间，不等于独立工具耗时）`,
          "mono",
        ),
      );
      if (tool.tests.length)
        block.append(el("pre", tool.tests.join("\n"), "test-evidence"));
      d.append(block);
    }
    if (r.assistant)
      d.append(fold("Agent 对外陈述（不等于验证结果）", r.assistant));
    card.append(d);
  }
  if (!t.rounds.length)
    card.append(el("p", "无可解析的原生轨迹；离线对照可能没有模型请求。"));
  card.append(el("p", "原始证据：" + t.evidence_path, "mono"));
  return card;
}
async function tracePicker(run, container) {
  const trials = await api(`/api/runs/${run.id}/traces`);
  const label = el("label", "选择 Agent / 任务轨迹"),
    select = el("select"),
    body = el("div");
  for (const t of trials) {
    const option = el(
      "option",
      `${t.agent} · ${t.trial} · ${reviewNames[t.review?.status] || "待复核"}`,
    );
    option.value = t.id;
    select.append(option);
  }
  const accepted = trials.find((t) => t.review?.status === "accepted");
  if (accepted) select.value = accepted.id;
  label.append(select);
  container.append(label, body);
  let version = 0;
  async function load() {
    const v = ++version;
    body.replaceChildren(el("p", "正在读取原生证据…"));
    try {
      const t = await api(`/api/runs/${run.id}/traces/${select.value}`);
      if (v === version) body.replaceChildren(traceCard(t));
    } catch (e) {
      if (v === version) body.replaceChildren(el("p", e.message, "error"));
    }
  }
  select.onchange = load;
  if (trials.length) await load();
  else body.append(el("p", "尚无已落盘任务轨迹。"));
}
async function renderEvidenceCompare(token) {
  await refreshRuns();
  if (epoch !== token) return;
  view.replaceChildren();
  const head = el("section", undefined, "page-head"),
    h = el("div");
  h.append(
    el("div", "EVIDENCE COMPARISON", "eyebrow"),
    el("h1", "从执行路线，解释结果差异。"),
    el("p", "默认选择已复核的真实结果；对比单位是一条 Agent 任务记录。"),
  );
  head.append(h, link("批次汇总对比", "#batch-compare", "button"));
  view.append(head);
  const options = [];
  for (const r of runs.filter((r) => r.mode === "live")) {
    for (const t of await api(`/api/runs/${r.id}/traces`))
      options.push({ run: r, trial: t });
  }
  if (epoch !== token) return;
  options.sort(
    (a, b) =>
      (b.trial.review?.status === "accepted") -
      (a.trial.review?.status === "accepted"),
  );
  const form = el("form", undefined, "panel compare-form"),
    selects = [];
  for (const title of ["轨迹 A", "轨迹 B"]) {
    const label = el("label", title),
      select = el("select");
    options.forEach((o, i) => {
      const op = el(
        "option",
        `${o.trial.agent} · ${o.trial.task || o.trial.trial} · ${o.run.name} · ${reviewNames[o.trial.review?.status] || "待复核"}`,
      );
      op.value = i;
      select.append(op);
    });
    label.append(select);
    form.append(label);
    selects.push(select);
  }
  if (options.length > 1) selects[1].value = "1";
  const btn = el("button", "分析执行差异 →", "button primary");
  btn.type = "submit";
  btn.disabled = options.length < 2;
  form.append(btn);
  const result = el("div");
  view.append(form, result);
  let version = 0;
  form.onsubmit = async (e) => {
    e.preventDefault();
    const v = ++version;
    btn.disabled = true;
    try {
      if (selects[0].value === selects[1].value)
        throw Error("请选择两条不同的轨迹");
      const chosen = selects.map((s) => options[Number(s.value)]);
      const [a, b, conditions] = await Promise.all([
        ...chosen.map((o) => api(`/api/runs/${o.run.id}/traces/${o.trial.id}`)),
        api(
          "/api/compare?" +
            new URLSearchParams({
              left: chosen[0].run.id,
              right: chosen[1].run.id,
            }),
        ),
      ]);
      if (epoch !== token || v !== version) return;
      result.replaceChildren();
      const matchedTask = Boolean(a.task) && a.task === b.task;
      const accepted = [a, b].every((t) => t.review.status === "accepted");
      result.append(
        el(
          "div",
          (conditions.compatible && matchedTask && accepted
            ? "任务与记录配置可配对，两条结果均已复核。"
            : "仅供查看：存在未复核/诊断记录、不同任务或配置差异。") +
            conditions.differences.join("；") +
            " 原生提示词、工具接口和 API 协议可能不同；单题单次不能证明普遍优劣，也不能精确归因全部耗时。",
          "callout",
        ),
      );
      const summary = el("section", undefined, "panel trace-card");
      summary.append(el("h2", "差异摘要 · 可直接核对的观测"));
      summary.append(
        table(
          ["指标", a.agent, b.agent, "如何理解"],
          [
            [
              "Agent 执行秒数",
              number(a.metrics.agent_seconds),
              number(b.metrics.agent_seconds),
              "运行器记录的总时间；不拆成无证据支持的模型/工具耗时",
            ],
            [
              "模型请求",
              number(a.metrics.requests),
              number(b.metrics.requests),
              "轮数更多会反复携带上下文；不等于每轮模型更慢",
            ],
            [
              "工具调用",
              number(a.metrics.tools),
              number(b.metrics.tools),
              "展开逐轮查看搜索、编辑、测试与恢复",
            ],
            [
              "平均每请求输入",
              number(
                a.metrics.input_tokens == null
                  ? null
                  : a.metrics.input_tokens / a.metrics.requests,
              ),
              number(
                b.metrics.input_tokens == null
                  ? null
                  : b.metrics.input_tokens / b.metrics.requests,
              ),
              "与请求数量一起解释累计输入",
            ],
            [
              "缓存命中输入",
              number(a.metrics.cached_tokens),
              number(b.metrics.cached_tokens),
              "累计输入含大量缓存；不能按全部未缓存输入估算费用",
            ],
            [
              "未缓存输入",
              number(a.metrics.uncached_tokens),
              number(b.metrics.uncached_tokens),
              "独立显示实际未命中部分；费用仍以供应商计费为准",
            ],
            [
              "错误信号",
              number(a.metrics.error_signals),
              number(b.metrics.error_signals),
              "文本检测提示，不能直接当失败率",
            ],
            [
              "最终补丁",
              a.patch_files.join("、"),
              b.patch_files.join("、"),
              a.patch && a.patch === b.patch
                ? "补丁完全一致"
                : "展开补丁核对具体修改，不据文件数判断质量",
            ],
          ],
        ),
      );
      summary.append(
        el(
          "p",
          "阅读顺序：先确认评分与补丁，再看测试输出，最后检查多出的请求和工具步骤。错误后的下一轮是否真正修复，请以返回内容为准。",
        ),
      );
      result.append(summary);
      const columns = el("div", undefined, "compare-columns trace-columns");
      columns.append(traceCard(a), traceCard(b));
      result.append(columns);
    } catch (e) {
      if (epoch === token) result.replaceChildren(el("p", e.message, "error"));
    } finally {
      btn.disabled = false;
    }
  };
  if (options.length >= 2) form.requestSubmit();
  else
    result.append(
      el("p", "至少需要两条真实模型轨迹；平台自测的轨迹可从各自详情查看。"),
    );
}
