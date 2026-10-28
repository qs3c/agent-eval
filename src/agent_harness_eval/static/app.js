"use strict";
const $ = (q, root = document) => root.querySelector(q);
const view = $("#view");
const labels = {
  queued: "排队中",
  running: "执行中",
  pausing: "等待在途尝试结束",
  paused: "已暂停",
  held: "队列已暂停",
  cancelling: "取消并清理中",
  cancelled: "已取消",
  completed: "已完成",
  failed: "执行异常",
  interrupted: "已中断",
  paused: "已暂停",
  incomplete: "结果不完整",
};
const modes = { offline: "离线对照", mock: "模拟模型", live: "真实模型" };
let runs = [],
  templates = [],
  filter = "real",
  search = "",
  epoch = 0,
  polling = false;
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
function notice(text = "") {
  $("#notice").textContent = text;
}
function number(x) {
  return x === null || x === undefined
    ? "未知"
    : Number(x).toLocaleString("zh-CN", { maximumFractionDigits: 3 });
}
function time(x) {
  const d = new Date(x);
  return Number.isNaN(+d)
    ? "时间未知"
    : d.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw Error("服务返回了无法读取的响应");
  }
  if (!response.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
function badge(status) {
  return el("span", labels[status] || status, "badge " + status);
}
function runState(run) {
  const summary = run.execution_summary;
  let label = labels[run.status] || run.status;
  if (summary?.finished && summary.errors > 0) {
    label = summary.errors === run.expected
      ? "已结束 · 全部异常" : "已结束 · 部分异常";
  } else if (summary?.finished && summary.unscored > 0) {
    label = "已结束 · 评分不完整";
  }
  const state = el("span", undefined, "run-state");
  state.append(el("span", label, "badge " + run.status));
  if (summary?.finished && (summary.errors > 0 || summary.unscored > 0)) {
    state.append(el("span",
      `${number(summary.errors)} 次运行异常 · ${number(summary.unscored)} 次未评分`,
      "run-sub"));
  }
  return state;
}
function link(text, href, cls) {
  const a = el("a", text, cls);
  a.href = href;
  return a;
}
function stats(items, target) {
  target.replaceChildren(
    ...items.map(([title, value, caption], i) => {
      const n = el("div", undefined, "stat" + (i === 0 ? " featured" : ""));
      n.append(
        el("div", title, "stat-label"),
        el("strong", value),
        el("small", caption),
      );
      return n;
    }),
  );
}
function table(headers, rows) {
  const wrap = el("div", undefined, "table-wrap"),
    t = el("table"),
    head = el("thead"),
    hr = el("tr"),
    body = el("tbody");
  headers.forEach((x) => hr.append(el("th", x)));
  head.append(hr);
  rows.forEach((cells) => {
    const tr = el("tr");
    cells.forEach((value) => {
      const td = el("td");
      if (value instanceof Node) td.append(value);
      else td.textContent = value;
      tr.append(td);
    });
    body.append(tr);
  });
  t.append(head, body);
  wrap.append(t);
  return wrap;
}
function routeInfo() {
  const [page, id] = location.hash.slice(1).split("/");
  return { page: page || "entry", id };
}
async function refreshRuns() {
  const data = await api("/api/runs");
  runs = data.runs;
  notice(data.blocked || "");
  return runs;
}
function renderRunRows() {
  const body = $("#run-rows");
  if (!body) return;
  const rows = runs.filter(
    (r) =>
      (filter === "all" ||
        (filter === "effective"
          ? r.accepted_agents.length > 0
          : filter === "debug"
            ? Object.values(r.reviews).some((x) => x.status === "diagnostic")
            : r.category === filter)) &&
      `${r.name} ${r.benchmark} ${r.agents.join(" ")} ${r.directory}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  body.replaceChildren();
  for (const r of rows) {
    const tr = el("tr"),
      name = el("td");
    name.append(
      link(r.name, "#run/" + r.id, "run-name"),
      el(
        "span",
        r.benchmark +
          " · " +
          { real: "真实评测", selftest: "平台自测", debug: "调试记录" }[
            r.category
          ] +
          " · " +
          r.directory,
        "run-sub",
      ),
    );
    const agents = el("td"),
      list = el("div", undefined, "agent-list");
    (filter === "effective" ? r.accepted_agents : r.agents).forEach((a) => {
      const item = el("span", a + " · " + reviewNames[r.reviews[a].status]);
      item.title = r.agent_descriptions[a] + "；" + r.reviews[a].reason;
      list.append(item);
    });
    agents.append(list);
    const mode = el("td");
    mode.append(el("span", modes[r.mode] || r.mode, "mode"));
    const state = el("td");
    state.append(runState(r));
    const progress = el("td", `${r.completed} / ${r.expected}`, "mono"),
      date = el("td", time(r.created), "mono"),
      action = el("td");
    const a = link("↗", "#run/" + r.id, "row-link");
    a.setAttribute("aria-label", "查看 " + r.name);
    action.append(a);
    tr.append(name, agents, mode, state, progress, date, action);
    body.append(tr);
  }
  $("#empty").hidden = rows.length > 0;
  if (!runs.length) {
    $("#empty").replaceChildren(el("p", "还没有实验记录。创建实验后，在这里查看进度与执行证据。"),
      link("查看使用引导 →", "#welcome", "button"), link("创建对比 →", "#setup", "button primary"));
  } else if (!rows.length) {
    const showAll = el("button", "查看全部记录（含离线与模拟实验）", "button");
    showAll.onclick = () => { filter = "all"; search = ""; renderRuns(epoch); };
    $("#empty").replaceChildren(el("p", "当前筛选没有匹配的实验，接入检查记录位于平台自测。"), showAll);
  }
  $("#run-count").textContent = rows.length;
  stats(
    [
      [
        "有效真实结果",
        runs.reduce((sum, r) => sum + r.accepted_agents.length, 0),
        "按复核后的 Agent 记录计，不等于题目数",
      ],
      [
        "正在执行",
        runs.filter((r) => ["running", "queued"].includes(r.status)).length,
        "后台队列 · 刷新页面不中断",
      ],
      [
        "执行已结束",
        runs.filter((r) => r.execution_summary?.finished || r.status === "completed").length,
        "包含部分异常；不等同于所有任务通过",
      ],
      [
        "需要关注",
        runs.filter((r) =>
          ["failed", "incomplete", "interrupted"].includes(r.status),
        ).length,
        "异常与缺失结果单独保留",
      ],
    ],
    $("#stats"),
  );
}
async function renderRuns(token) {
  view.replaceChildren($("#runs-template").content.cloneNode(true));
  $("#filters")
    .querySelectorAll("button")
    .forEach((b) => b.classList.toggle("active", b.dataset.filter === filter));
  $("#search").value = search;
  $("#search").oninput = (e) => {
    search = e.target.value;
    renderRunRows();
  };
  $("#filters").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    filter = b.dataset.filter;
    $("#filters")
      .querySelectorAll("button")
      .forEach((x) => x.classList.toggle("active", x === b));
    renderRunRows();
  };
  await refreshRuns();
  if (epoch !== token) return;
  renderRunRows();
}
function checks(target, name, values) {
  target.replaceChildren(
    ...values.map((value) => {
      const l = el("label", undefined, "check-card"),
        input = el("input");
      input.type = "checkbox";
      input.name = name;
      input.value = value;
      input.checked = true;
      l.append(input, el("span", value));
      return l;
    }),
  );
}
async function renderCreate(token) {
  const [data, modelPool] = await Promise.all([api("/api/templates"), api("/api/models")]);
  if (epoch !== token) return;
  templates = data.templates;
  notice(data.blocked || "");
  view.replaceChildren($("#create-template").content.cloneNode(true));
  const select = $("#template"),
    form = $("#create-form");
  const poolSection = rsiPanel("模型分配", "从全局模型池选择；本次选择随实验冻结，不修改场景默认模型。");
  const experimentModel = poolSelect(modelPool, "实验模型", modelPool.bindings.experiment, "使用场景默认 / 模板模型");
  const guardianModel = poolSelect(modelPool, "守护模型", modelPool.bindings.guardian, "使用场景默认 / 模板守护配置");
  const modelHint = el("p", undefined, "muted"), manage = el("a", "管理全局模型池 →"); manage.href = "#models";
  poolSection.append(rsiField("实验模型", experimentModel), rsiField("守护模型", guardianModel), modelHint, manage);
  form.querySelector(".create-grid > div").append(poolSection);
  templates.forEach((t) => {
    const option = el("option", t.name + " · " + modes[t.mode]);
    option.value = t.id;
    select.append(option);
  });
  function updateCount() {
    const count =
      form.querySelectorAll("[name=agents]:checked").length *
      form.querySelectorAll("[name=tasks]:checked").length *
      Number(form.elements.attempts.value);
    $("#expected").textContent = number(count);
  }
  function choose() {
    const t = templates.find((t) => t.id === select.value);
    if (!t) {
      $("#submit").disabled = true;
      $("#template-info").textContent = "未发现 experiments 目录中的有效模板。";
      return;
    }
    $("#experiment-name").value =
      t.name +
      "-" +
      new Date()
        .toISOString()
        .replace(/[-:TZ.]/g, "")
        .slice(0, 14);
    Object.entries(t.defaults).forEach(([k, v]) => {
      const input = form.elements[k];
      input.value = v ?? "";
      if (["timeout_seconds", "cpus", "memory_mb"].includes(k)) {
        input.required = v != null;
        input.placeholder = v == null ? "使用每道题的配置" : "";
      }
    });
    checks($("#agent-options"), "agents", t.agents);
    checks($("#task-options"), "tasks", t.tasks);
    $("#template-info").textContent =
      t.error || `${t.benchmark} · ${modes[t.mode]} · 输入依赖已找到 · ${datasetLabel(t.dataset)}`;
    $("#template-info").classList.toggle("warning", !t.available);
    $("#submit").disabled = !t.available || Boolean(data.blocked);
    experimentModel.disabled = t.mode !== "live";
    guardianModel.disabled = t.guardian_mode !== "assist";
    modelHint.textContent = t.mode !== "live" ? "此模板为离线或模拟实验，保留原模型契约。" : "实验 Agent 共用所选模型；守护模型仅在自主介入模式生效。";
    modelSummary();
    modelSummary();
    form.elements.confirm_unbounded.checked = false;
    $("#official-regrade-option").hidden = t.official_regrade == null;
    $("#official-regrade-hint").hidden = t.official_regrade == null;
    form.elements.official_regrade.checked = t.official_regrade === true;
    updateCount();
  }
  function modelSummary() {
    const t = templates.find(t => t.id === select.value); if (!t) return;
    const selected = modelPool.models.find(m => m.id === (experimentModel.value || modelPool.bindings.experiment));
    $("#summary-model").textContent = `${modes[t.mode]} / ${t.mode === "live" && selected ? selected.label + " · " + selected.name : t.model}`;
    $("#live-confirm").hidden = t.mode !== "live" && !(t.guardian_mode === "assist" && guardianModel.value);
  }
  experimentModel.onchange = modelSummary; guardianModel.onchange = modelSummary;
  select.onchange = choose;
  form.oninput = updateCount;
  choose();
  form.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(form),
      payload = {
        template: fd.get("template"),
        name: fd.get("name"),
        agents: fd.getAll("agents"),
        tasks: fd.getAll("tasks"),
        confirm_unbounded: fd.has("confirm_unbounded"),
      };
    if (!experimentModel.disabled && experimentModel.value) payload.model_id = experimentModel.value;
    if (!guardianModel.disabled && guardianModel.value) payload.guardian_model_id = guardianModel.value;
    for (const k of [
      "attempts",
      "concurrency",
      "timeout_seconds",
      "cpus",
      "memory_mb",
    ]) {
      const value = fd.get(k);
      if (value != null && String(value).trim() !== "") payload[k] = Number(value);
    }
    if (!payload.agents.length || !payload.tasks.length) {
      $("#form-error").textContent = "请至少选择一个 Agent 和一个任务。";
      return;
    }
    if (templates.find(t => t.id === select.value)?.official_regrade != null) {
      payload.official_regrade = form.elements.official_regrade.checked;
    }
    const button = $("#submit");
    button.disabled = true;
    button.textContent = "正在保存输入并创建…";
    $("#form-error").textContent = "";
    try {
      const result = await api("/api/runs", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      location.hash = "#run/" + result.id;
    } catch (error) {
      $("#form-error").textContent = error.message;
      button.disabled = false;
      button.textContent = "创建并加入队列 →";
    }
  };
}
function tokenUsageLabel(total, evidence = {}) {
  if (total != null) return number(total);
  const known = evidence.known;
  const notes = [];
  if (evidence.missing_requests) notes.push(`${evidence.missing_requests} 个请求缺少用量`);
  notes.push(...(evidence.issues || []));
  return `${known != null ? `已记录 ≥ ${number(known)}` : "用量未知"}（不完整${notes.length ? "；" + notes.join("、") : ""}）`;
}
function agentRows(report) {
  if (!report) return [];
  return Object.entries(report.agents).map(([id, a]) => {
    const trials = report.trials.filter((t) => t.agent === id);
    const total = (key) =>
      trials.length === a.expected &&
      trials.every((t) => t[key] !== null && t[key] !== undefined)
        ? trials.reduce((s, t) => s + t[key], 0)
        : null;
    const tokens = (key) => {
      const values = trials.map(t => t[key] ?? t.usage_evidence?.known_usage?.[key]).filter(v => Number.isFinite(v) && v >= 0);
      return tokenUsageLabel(total(key), {
        known: values.length ? values.reduce((s, v) => s + v, 0) : null,
        missing_requests: trials.reduce((s, t) => s + (t.usage_evidence?.missing_usage_requests || 0), 0),
        issues: [...new Set(trials.flatMap(t => Object.keys(t.usage_evidence?.usage_issues || {})))],
      });
    };
    return [
      id,
      `${a.recorded} / ${a.expected}`,
      number(a.mean_on_scored),
      `${a.scored} / ${a.expected}`,
      number(a.errors),
      number(a.missing),
      number(total("agent_seconds")),
      tokens("input_tokens"),
      tokens("output_tokens"),
      number(a.cost_usd),
    ];
  });
}
function reportTable(report, reviews = {}) {
  return table(
    [
      "Agent",
      "已结束 / 应有",
      "已评分均值",
      "已评分 / 应有",
      "异常",
      report.live ? "尚未结束" : "缺失",
      "Agent 秒数",
      "输入 tokens",
      "输出 tokens",
      "费用 USD",
    ],
    agentRows(report).map((row) => {
      const review = reviews[row[0]];
      if (review?.status === "diagnostic") {
        row[0] += " · 仅供诊断";
        row[7] = row[8] = "不纳入对比";
      }
      return row;
    }),
  );
}
function regradingPanel(run) {
  const panel = el("section", undefined, "panel");
  panel.id = "official-regrading";
  panel.append(el("h2", "官方复评"), el("p", "同一批次只提交一个任务，不同批次排队；全局并发度 2。", "muted"));
  if (run.official_regrading?.supported) {
    const label = el("label", undefined, "check-card"), toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = run.official_regrading.enabled;
    label.append(toggle, document.createTextNode("自动官方复评"));
    panel.append(label, el("p", "关闭后使用 Harbor 原始评分；对尚未开始的自动复评生效。", "muted"));
    toggle.onchange = async () => {
      toggle.disabled = true;
      try {
        await api(`/api/runs/${run.id}/official-regrading`, {method:"POST", body:JSON.stringify({enabled:toggle.checked})});
        notice(toggle.checked ? "自动官方复评已开启" : "自动官方复评已关闭，保留 Harbor 评分");
        await renderDetail(run.id, epoch, false);
      } catch (error) { toggle.checked = !toggle.checked; notice(error.message); }
      finally { toggle.disabled = false; }
    };
  }
  const names = {pending:"待复评",failed:"复评未成功",interrupted:"复评已中断",queued:"已排队",running:"复评中",completed:"复评完成",disabled:"已关闭"};
  const batches = run.official_regrading?.batches || [];
  async function submit(batch) {
    return api(`/api/runs/${run.id}/batches/${batch.number}/regrade`, {method:"POST", body:"{}"});
  }
  const all = el("button", "复评全部未完成批次", "button");
  const pending = batches.filter(b => b.eligible !== false && !["completed", "queued", "running"].includes(b.status));
  all.disabled = !pending.length;
  all.onclick = async () => {
    all.disabled = true;
    try { for (const batch of pending) await submit(batch); notice("复评已提交，按批次排队执行"); }
    catch (error) { all.disabled = false; notice(error.message); }
  };
  panel.append(all);
  for (const batch of batches) {
    const row = el("div", undefined, "detail-top");
    const progress = batch.progress;
    row.append(el("span", `批次 ${batch.number} · ${names[batch.status] || batch.status}`));
    if (!progress && batch.expected_units) row.append(el("span", `待复评 ${batch.expected_units} 项`, "muted"));
    if (progress) {
      row.append(el("span", `已完成 ${progress.completed}/${progress.total} 项`, "muted"));
      for (const unit of Object.values(progress.units || {})) {
        if (["running", "retrying", "failed"].includes(unit.status)) {
          row.append(el("span", `${unit.agent} · ${unit.task} · 第 ${unit.retry || 1}/3 次 · ${unit.error || (unit.status === "retrying" ? "等待重试" : "复评中")}`, "muted"));
        }
      }
    }
    if (batch.status !== "completed" && batch.job?.error) row.append(el("span", batch.job.error, "error"));
    const button = el("button", "复评未完成题目", "button");
    button.disabled = batch.eligible === false || ["queued", "running", "completed"].includes(batch.status);
    button.onclick = async () => {
      button.disabled = true;
      try { await submit(batch); button.textContent = "已排队"; notice("该批次复评已提交"); }
      catch (error) { button.disabled = false; notice(error.message); }
    };
    const log = el("button", "查看复评日志", "button");
    log.onclick = async () => {
      try {
        const data = await api(`/api/runs/${run.id}/batches/${batch.number}/regrade-log`);
        let output = row.querySelector("pre");
        if (!output) { output = el("pre"); row.append(output); }
        output.textContent = data.logs.map(item => `${item.agent} · ${item.task}\n${item.text || item.error}`).join("\n\n") || "暂无复评日志";
      } catch (error) { notice(error.message); }
    };
    row.append(button, log);
    panel.append(row);
  }
  return panel;
}

function refreshDetailTraces(run) {
  const existing = $("#detail-traces");
  if (!existing) return;
  const signature = JSON.stringify(run.trials.map(t => t.id));
  if (existing.dataset.signature === signature) return;
  const traces = el("section", undefined, "trace-section");
  traces.id = "detail-traces"; traces.dataset.signature = signature;
  traces.append(el("h2", "执行轨迹与证据"));
  existing.replaceWith(traces);
  tracePicker(run, traces).catch(e => traces.append(el("p", e.message, "error")));
}
async function renderDetail(id, token, initial = true) {
  const [r, logs, incidents] = await Promise.all([
    api("/api/runs/" + id),
    api("/api/runs/" + id + "/logs"),
    api("/api/runs/" + id + "/incidents"),
  ]);
  if (epoch !== token) return;
  if (initial) {
    view.replaceChildren();
    const head = el("section", undefined, "page-head"),
      left = el("div");
    left.append(
      el("div", "RUN DETAILS", "eyebrow"),
      el("h1", r.name, "detail-title"),
      el("p", r.directory),
    );
    head.append(left, link("对比本实验 Agent →", "#compare/" + r.id, "button primary"), link("← 实验记录", "#runs", "button"));
    const state = el("div", undefined, "detail-top");
    state.id = "detail-state";
    const metrics = el("section", undefined, "stats");
    metrics.id = "detail-stats";
    const results = el("section", undefined, "panel");
    const heading = el("div", undefined, "panel-heading"),
      headingText = el("div");
    headingText.append(
      el(
        "h2",
        Object.values(r.reviews).some((x) => x.status === "diagnostic")
          ? "原始批次结果 · 含诊断条目"
          : "原始批次结果",
      ),
      el("p", r.grading),
    );
    heading.append(headingText);
    const scoreBody = el("div");
    scoreBody.id = "scores";
    results.append(heading);
    for (const a of r.agents)
      results.append(
        el(
          "p",
          `${a} · ${r.agent_descriptions[a]} · ${reviewNames[r.reviews[a].status]}：${r.reviews[a].reason}`,
          "callout",
        ),
      );
    results.append(scoreBody);
    const grids = el("section", undefined, "detail-grid"),
      logPanel = el("div", undefined, "panel");
    logPanel.append(el("h2", "执行日志 · 最近 32 KiB"));
    const pre = el("pre");
    pre.id = "logs";
    pre.setAttribute("aria-label", "执行日志");
    logPanel.append(pre);
    const configPanel = el("div", undefined, "panel");
    configPanel.append(el("h2", "配置与证据"), el("p", r.trajectory_note));
    const details = el("details"),
      summary = el("summary", "查看冻结配置"),
      conf = el("pre", JSON.stringify(r.experiment, null, 2));
    details.append(summary, conf);
    const info = el("p", "原始证据：artifacts/" + r.directory, "section-space");
    configPanel.append(info, details);
    grids.append(logPanel, configPanel);
    const traces = el("section", undefined, "trace-section");
    traces.id = "detail-traces";
    const guardian = el("section", undefined, "panel");
    guardian.id = "guardian-incidents";
    view.append(head, state, metrics, results, guardian, traces, grids);
  }
  refreshDetailTraces(r);
  $("#detail-state").replaceChildren(
    runState(r),
    el("span", modes[r.mode] + " · " + r.benchmark, "muted"),
  );
  if (r.error) $("#detail-state").append(el("span", r.error, "error"));
  if (r.batch_state) {
    const phases = {preparing:"准备环境",running:"执行 Agent",regrading:"官方复评",cleaning:"清理环境",completed:"已完成",failed:"批次失败",interrupted:"已中断",paused:"已暂停"};
    const b = r.batch_state;
    $("#detail-state").append(el("span", `批次 ${b.current_batch || 1}/${b.total_batches || Math.ceil(b.total_tasks / b.batch_size)} · ${phases[b.phase] || b.phase} · 每批最多 ${b.batch_size} 题`, "muted"));
    const pendingRegrades = (r.official_regrading?.batches || []).filter(
      batch => !["completed", "disabled"].includes(batch.status),
    );
    if (pendingRegrades.length) {
      $("#detail-state").append(el("span",
        `官方复评待处理：批次 ${pendingRegrades.map(batch => batch.number).join("、")}；作答进度独立统计`,
        "error"));
    }
  }
  if (r.official_regrading?.batches?.length) {
    const existing = $("#official-regrading");
    const panel = regradingPanel(r);
    if (existing) existing.replaceWith(panel);
    else $("#detail-state").after(panel);
  }
  stats(
    [
      [
        "已结束尝试",
        `${r.completed} / ${r.expected}`,
        "根据已落盘的终态结果统计",
      ],
      ["参与 Agent", r.agents.length, "各自保留原生执行循环"],
      ["每题重复", r.experiment.attempts, "每次尝试独立计入结果"],
      [
        "任务并发",
        r.experiment.concurrency,
        `CPU：${r.experiment.cpus ?? "按题目配置"} / 内存：${r.experiment.memory_mb == null ? "按题目配置" : r.experiment.memory_mb + " MiB"}`,
      ],
    ],
    $("#detail-stats"),
  );
  $("#scores").replaceChildren(
    r.report
      ? reportTable(r.report, r.reviews)
      : r.trials.length
        ? table(
            ["Agent", "任务", "状态", "主指标", "Agent 秒数"],
            r.trials.map((t) => [
              t.agent,
              t.task,
              t.exception ? "执行异常" : "已结束",
              number(t.metrics[r.experiment.benchmark.primary_metric]),
              number(t.agent_seconds),
            ]),
          )
        : el(
            "div",
            r.status === "queued"
              ? "已加入队列，等待前序实验结束。"
              : r.status === "running"
                ? "正在执行。任务完成后显示结果；最终报告在实验结束时生成。"
                : "没有可用的结果报告，请检查执行日志。",
            "empty",
          ),
  );
  $("#logs").textContent = logs.text;
  const guardian = $("#guardian-incidents");
  const assisted = r.trials.filter(t => t.guardian?.assisted).length;
  const incidentSignature = JSON.stringify([incidents, assisted, r.experiment.guardian]);
  if (guardian.dataset.signature === incidentSignature) return;
  guardian.dataset.signature = incidentSignature;
  guardian.replaceChildren(el("h2", "实验运行守护"), el("p",
    `${r.experiment.guardian ? (r.experiment.guardian.mode === "assist" ? "自主介入模式" : "观察与期限兜底模式") : "本实验未配置通用守护"} · ${incidents.incidents.length} 条事件 · ${assisted} 次外部辅助尝试`));
  if (assisted) guardian.append(el("p", "原始评分保留；受辅助尝试不能解释为 Agent 独立完成。", "callout"));
  if (!incidents.incidents.length) guardian.append(el("p", "暂无守护事件。历史人工介入材料仍保存在原实验目录。", "muted"));
  for (const incident of incidents.incidents) {
    const button = el("button", `${incident.agent} / ${incident.trial} · ${incident.reason} · ${incident.outcome.status || "调查中"}${incident.assisted ? " · 已介入" : ""}`, "button");
    button.type = "button";
    button.onclick = async () => {
      try {
        const data = await api(`/api/runs/${encodeURIComponent(id)}/incidents/${incident.id}`);
        if (epoch !== token || !button.isConnected) return;
        const content = el("div");
        content.append(el("p", data.summary || "守护尚未提交诊断总结。"), el("p", `证据：${data.directory}`),
          table(["时间", "记录", "工具 / 动作", "命令", "退出码 / 错误"], data.actions.map(a =>
            [time(a.time), a.kind, a.tool || "", a.command || "", a.error || (a.returncode ?? "")])), el("p", data.note, "muted"));
        button.replaceWith(content);
      } catch (error) { notice(error.message); }
    };
    guardian.append(button);
  }
  if (incidents.truncated) guardian.append(el("p", "事件超过页面展示上限，请读取原始实验目录。"));
}
async function renderCompare(token) {
  await refreshRuns();
  if (epoch !== token) return;
  const experiments = runs.filter(run => run.mode === "live");
  view.replaceChildren();
  const head = el("section", undefined, "page-head"),
    left = el("div");
  left.append(
    el("div", "COMPARE RUNS", "eyebrow"),
    el("h1", "跨实验批次对比"),
    el("p", "核对任务、模型与运行条件，再查看各项结果。"),
  );
  head.append(left);
  head.append(link("← 实验内 Agent 对比", "#compare", "button"), link("跨实验轨迹查看", "#trace-compare", "button"));
  const form = el("form", undefined, "panel compare-form");
  for (const [name, title] of [
    ["left", "实验 A"],
    ["right", "实验 B"],
  ]) {
    const label = el("label", title),
      select = el("select");
    select.name = name;
    select.required = true;
    experiments.forEach((r) => {
      const opt = el(
        "option",
        `${r.name} · ${time(r.created)} · ${labels[r.status]}`,
      );
      opt.value = r.id;
      select.append(opt);
    });
    label.append(select);
    form.append(label);
  }
  if (experiments.length > 1) form.elements.right.selectedIndex = 1;
  const button = el("button", "对比结果 →", "button primary");
  button.type = "submit";
  button.disabled = experiments.length < 2;
  form.append(button);
  const result = el("div");
  result.id = "compare-result";
  view.append(head, form, result);
  if (experiments.length < 2)
    result.append(el("div", "至少需要两次真实模型实验记录才能比较。", "empty"));
  form.onsubmit = async (e) => {
    e.preventDefault();
    button.disabled = true;
    try {
      const fd = new FormData(form);
      if (fd.get("left") === fd.get("right"))
        throw Error("请选择两次不同的实验。");
      const data = await api(
        "/api/compare?" +
          new URLSearchParams({ left: fd.get("left"), right: fd.get("right") }),
      );
      result.replaceChildren();
      const message = el(
        "div",
        data.compatible
          ? "条件一致，可查看逐任务配对结果。单次实验不构成整体能力排名。"
          : "暂不能直接配对比较：" + data.differences.join("；"),
        "callout" + (data.compatible ? "" : " warning"),
      );
      result.append(message);
      const columns = el("div", undefined, "compare-columns");
      for (const [label, r] of [
        ["A", data.left],
        ["B", data.right],
      ]) {
        const panel = el("section", undefined, "panel");
        panel.append(
          el("div", "EXPERIMENT " + label, "eyebrow"),
          el("h2", r.name),
          el("p", datasetLabel(r.experiment.benchmark.dataset)),
          el(
            "p",
            `${r.benchmark} · ${modes[r.mode]} · ${r.experiment.model.name}`,
          ),
          el("p", r.grading),
          r.report ? reportTable(r.report, r.reviews) : el("p", "报告缺失"),
        );
        const details = el("details"),
          summary = el("summary", "查看运行条件"),
          pre = el(
            "pre",
            JSON.stringify(
              {
                model: r.experiment.model,
                attempts: r.experiment.attempts,
                task_hashes: r.task_hashes,
              },
              null,
              2,
            ),
          );
        details.append(summary, pre);
        panel.append(details);
        if (data.compatible && r.report) {
          panel.append(
            el("h3", "逐任务已评分均值"),
            table(
              ["任务", ...r.agents],
              Object.entries(r.report.paired_tasks || {}).map(
                ([task, values]) => [
                  task,
                  ...r.agents.map((a) => number(values[a])),
                ],
              ),
            ),
          );
        }
        columns.append(panel);
      }
      result.append(columns);
    } catch (error) {
      result.replaceChildren(el("div", error.message, "error"));
    } finally {
      button.disabled = false;
    }
  };
}
async function route() {
  window.scrollTo(0, 0);
  const token = ++epoch;
  let { page, id } = routeInfo();
  notice();
  let landingData;
  if (page === "entry") {
    view.replaceChildren(el("p", "正在打开工作空间…", "landing-loading"));
    landingData = await loadLandingData();
    if (epoch !== token) return;
    page = landingEntry(landingData);
    history.replaceState(null, "", "#" + page);
  }
  if (typeof updateGuideReturn === "function") updateGuideReturn(page);
  $("#breadcrumb").textContent =
    page === "home" ? "工作台首页" : page === "welcome" ? "使用引导" : page === "setup" ? "创建对比" : page === "integrations" ? "Agent 接入" : page === "models" ? "模型设置" : ["rsi", "rsi-case"].includes(page) ? "RSI 改进" : page === "create"
      ? "创建实验"
      : ["compare", "batch-compare", "trace-compare"].includes(page)
        ? "结果对比"
        : page === "run"
          ? "运行详情"
          : "实验记录";
  document.querySelectorAll("[data-nav]").forEach((n) => {
    const active = n.dataset.nav === (page === "rsi-case" ? "rsi" : page === "run" ? "runs" : ["batch-compare", "trace-compare"].includes(page) ? "compare" : page);
    n.classList.toggle("active", active);
    if (active) n.setAttribute("aria-current", "page");
    else n.removeAttribute("aria-current");
  });
  if (["rsi", "rsi-case"].includes(page)) {
    const loading = el("div", "正在加载改进档案…", "rsi-loading");
    loading.setAttribute("role", "status");
    view.replaceChildren(loading);
  }
  try {
    if (page === "home") await renderHome(token, landingData);
    else if (page === "welcome") await renderWelcome(token, landingData);
    else if (page === "setup") await renderLocalSetup(token);
    else if (page === "integrations") await renderIntegrations(token);
    else if (page === "create") await renderCreate(token);
    else if (page === "models") await renderModels(token);
    else if (page === "rsi") await renderRsi(token, id);
    else if (page === "rsi-case") await renderRsiCase(token, id);
    else if (page === "compare") await renderExperimentCompare(token, id);
    else if (page === "trace-compare") await renderEvidenceCompare(token);
    else if (page === "batch-compare") await renderCompare(token);
    else if (page === "run" && id) await renderDetail(id, token);
    else await renderRuns(token);
  } catch (error) {
    if (epoch === token) {
      view.replaceChildren(el("div", error.message, "empty"));
      notice("加载失败。请确认本机服务仍在运行，刷新页面后重试。");
    }
  }
}
window.addEventListener("hashchange", route);
route();
setInterval(async () => {
  if (polling || document.hidden) return;
  const { page, id } = routeInfo(),
    token = epoch;
  if (!["runs", "run"].includes(page) && !(page === "rsi" && id)) return;
  polling = true;
  try {
    if (page === "rsi") await renderRsi(token, id, false);
    else if (page === "runs") {
      await refreshRuns();
      if (epoch === token) renderRunRows();
    } else await renderDetail(id, token, false);
    $("#connection").textContent = "● 本机服务 · 已连接";
  } catch (error) {
    notice("自动刷新失败：" + error.message);
    $("#connection").textContent = "连接中断";
  } finally {
    polling = false;
  }
}, 2500);
