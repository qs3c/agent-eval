"use strict";

const rsiPhases = { created: "待改进", improving: "分析与修改中", checking: "检查中", validated: "检查通过",
  failed: "改进失败", interrupted: "已中断", queued: "排队中", running: "执行中", completed: "已完成" };
const rsiAssessments = { improved: "本组评测改善", equal: "本组评测持平", regressed: "本组评测下降", inconclusive: "证据不足以比较", failed: "复评执行失败", running: "复评中" };
const rsiCategories = { agent_provenance: "源码版本", trial_artifact: "任务产物", agent_trace: "Agent 轨迹", verifier_artifact: "评分器输出",
  guardian_incident: "守护事件", manual_intervention: "人工介入记录", manual_intervention_index: "人工介入索引",
  manual_diagnostic_snapshot: "人工诊断快照", legacy_run_watchdog: "历史监控记录" };
const rsiActionNames = { improve: "分析与改进", evaluate: "父子复评", publish: "发布草稿 PR" };
const rsiExecutorNames = { "builtin-bkcrab": "BKCrab", "builtin-dsh": "DSH" };
const rsiUi = { tab: "cases", search: "", evidence: new Map(), pending: false };

function rsiSelect(items, label) {
  const select = el("select");
  for (const [value, text] of items) {
    const option = el("option", text); option.value = value; select.append(option);
  }
  select.setAttribute("aria-label", label);
  return select;
}
function rsiField(label, input, hint) {
  const wrapper = el("label", undefined, "rsi-field");
  wrapper.append(el("span", label), input);
  if (hint) wrapper.append(el("small", hint));
  return wrapper;
}
function rsiButton(text, action, primary = false) {
  const button = el("button", text, "button" + (primary ? " primary" : ""));
  button.type = "button"; button.onclick = action; return button;
}
function rsiBadge(text, tone = "neutral") { return el("span", text, "rsi-badge " + tone); }
function rsiBytes(value) {
  if (value == null) return "大小未知";
  if (value < 1024) return value + " B";
  return value < 1048576 ? (value / 1024).toFixed(1) + " KiB" : (value / 1048576).toFixed(2) + " MiB";
}
function rsiTitle(source) {
  const first = source.trials[0] || "未记录尝试";
  const task = first.includes("__") ? first.split("__").slice(0, -1).join(" / ") : first;
  return task + (source.trials.length > 1 ? ` 等 ${source.trials.length} 次尝试` : "");
}
function rsiEmpty(title, description, action) {
  const box = el("div", undefined, "rsi-empty");
  box.append(el("div", "↗", "rsi-empty-icon"), el("h3", title), el("p", description));
  if (action) box.append(action);
  return box;
}
function rsiPanel(title, description) {
  const panel = el("section", undefined, "panel rsi-panel"), head = el("div", undefined, "rsi-section-head");
  head.append(el("h2", title));
  if (description) head.append(el("p", description));
  panel.append(head); return panel;
}
function rsiShell() {
  const shell = el("div", undefined, "rsi-workbench");
  view.replaceChildren(shell); return shell;
}
function rsiHeader(title, description, actions = []) {
  const head = el("section", undefined, "page-head rsi-head"), text = el("div"), buttons = el("div", undefined, "rsi-actions");
  text.append(el("div", "RSI / 改进工作台", "eyebrow"), el("h1", title), el("p", description));
  buttons.append(...actions); head.append(text, buttons); return head;
}
function rsiConfigNote(data) {
  const config = data.executor_settings;
  if (!config) return null;
  const token = epoch, box = rsiPanel("执行 Agent", "选择负责分析证据和修改代码的 Agent，并从全局模型池指定模型。");
  box.classList.add("rsi-executor-panel");
  const form = el("form", undefined, "rsi-executor-form"), row = el("div", undefined, "rsi-executor-row");
  const select = rsiSelect(config.options.map(o => [o.id, o.name]), "执行 Agent"); select.value = config.executor;
  const model = rsiSelect([["", "请选择全局模型"], ...(config.models || []).map(m => [m.id, m.label + " · " + m.name])], "执行模型");
  const timeout = rsiSelect([["600", "10 分钟"], ["1800", "30 分钟"], ["3600", "60 分钟"], ["7200", "120 分钟"]], "执行时限");
  if (![600, 1800, 3600, 7200].includes(config.timeout_seconds)) { const o = el("option", config.timeout_seconds + " 秒"); o.value = config.timeout_seconds; timeout.append(o); }
  timeout.value = String(config.timeout_seconds);
  const save = rsiButton("保存设置", null, true); save.type = "submit";
  const status = el("div", undefined, "rsi-executor-status"), error = el("p", undefined, "error"); error.setAttribute("role", "alert");
  const link = el("a", "管理全局模型池 →", "rsi-model-link"); link.href = "#models";
  function statusUpdate() {
    const chosen = config.options.find(o => o.id === select.value);
    const selected = (config.models || []).find(m => m.id === model.value);
    const ready = chosen.installed && selected?.has_api_key && (select.value !== "dsh" || selected.messages_base);
    status.replaceChildren(rsiBadge(!chosen.installed ? "运行环境未就绪" : !selected ? "请选择模型" : !selected.has_api_key ? "模型缺少凭据" : select.value === "dsh" && !selected.messages_base ? "DSH 需要 Messages 接口" : config.configured && select.value === config.executor && model.value === config.model_id ? "已启用" : "可启用", ready ? "success" : "warning"));
    save.disabled = !ready;
  }
  function choose() { model.value = config.bindings?.["executor_" + select.value] || ""; statusUpdate(); }
  select.onchange = choose; model.onchange = statusUpdate; choose();
  row.append(rsiField("执行 Agent", select), rsiField("执行模型", model), rsiField("执行时限", timeout), save);
  form.append(row, status, link, error); box.append(form);
  form.onsubmit = async event => {
    event.preventDefault(); if (save.disabled) return;
    save.disabled = true; error.textContent = "";
    try {
      await api("/api/rsi/executor-settings", {method:"PUT", body:JSON.stringify({executor:select.value, model_id:model.value, timeout_seconds:Number(timeout.value)})});
      if (epoch === token) { const route = routeInfo(); if (route.page === "rsi-case") await renderRsiCase(token, route.id); else await renderRsi(token); notice("执行 Agent 与模型选择已保存。"); }
    } catch (err) {error.textContent = err.message;}
    finally {statusUpdate();}
  };
  return box;
}

async function renderRsi(token, identity, initial = true) {
  if (identity) return renderRsiCandidate(token, identity, initial);
  const [data, experiments] = await Promise.all([api("/api/rsi"), api("/api/runs")]);
  if (epoch !== token) return;
  const shell = rsiShell();
  const refresh = rsiButton("刷新", async () => {
    refresh.disabled = true;
    try { await renderRsi(token); } catch (error) { if (epoch === token) notice(error.message); }
    finally { refresh.disabled = false; }
  });
  shell.append(rsiHeader("让每一次改进都有据可查", "从失败案例出发，生成候选版本，再用配对复评验证效果。", [refresh,
    rsiButton("＋ 新建诊断案例", () => rsiCaseDialog(token, experiments), true)]));
  const counts = el("section", undefined, "rsi-metrics");
  for (const [label, value, hint] of [["诊断案例", data.cases.length, "已保留的证据档案"], ["候选版本", data.candidates.length, "每个版本独立留存"],
    ["进行中的操作", data.actions.filter(a => ["queued", "running"].includes(a.status)).length, "执行中与排队中"]]) {
    const card = el("div", undefined, "rsi-metric");
    card.append(el("span", label), el("strong", value), el("small", hint)); counts.append(card);
  }
  shell.append(counts);
  const flow = el("ol", undefined, "rsi-workflow");
  for (const [index, title, desc] of [["01", "冻结证据", "保留问题现场"], ["02", "生成候选", "诊断与代码修改"], ["03", "检查与复评", "验证改进效果"], ["04", "草稿 PR", "交付可审阅变更"]]) {
    const step = el("li"), text = el("div"); text.append(el("strong", title), el("small", desc));
    step.append(el("span", index, "rsi-step-number"), text); flow.append(step);
  }
  shell.append(flow);
  const config = rsiConfigNote(data); if (config) shell.append(config);
  const archive = rsiPanel("改进档案", "案例保存证据，候选记录从修改到验证的每一步。"), toolbar = el("div", undefined, "rsi-toolbar");
  const tabs = el("div", undefined, "rsi-tabs"); tabs.setAttribute("role", "group"); tabs.setAttribute("aria-label", "档案类型");
  const search = el("input"); search.type = "search"; search.placeholder = "搜索任务、Agent、目标或编号";
  search.setAttribute("aria-label", "搜索改进档案"); search.value = rsiUi.search;
  const body = el("div", undefined, "rsi-archive-body");
  const tabButtons = [];
  function draw() {
    for (const [name, button] of tabButtons) { button.classList.toggle("active", rsiUi.tab === name); button.setAttribute("aria-pressed", String(rsiUi.tab === name)); }
    body.replaceChildren();
    const query = rsiUi.search.trim().toLowerCase();
    if (rsiUi.tab === "cases") {
      const matches = data.cases.filter(c => `${c.id} ${c.agent} ${c.trials.join(" ")} ${c.objective}`.toLowerCase().includes(query));
      if (!matches.length) body.append(rsiEmpty(query ? "没有匹配的案例" : "从一个问题开始", query ? "试试任务名称、Agent 或案例编号。" : "选择已结束实验，把异常尝试和改进目标保存为诊断案例。",
        query ? null : rsiButton("新建诊断案例", () => rsiCaseDialog(token, experiments))));
      for (const c of matches) {
        const card = el("article", undefined, "rsi-case-card"), main = el("div", undefined, "rsi-case-main"), meta = el("div", undefined, "rsi-meta");
        meta.append(rsiBadge(c.agent), rsiBadge(c.status === "ready" ? "证据已冻结" : "采集中", c.status === "ready" ? "success" : "warning"), el("span", `案例 ${c.id.slice(0, 8)}`, "mono"));
        const title = el("h3"); title.append(link(rsiTitle(c), "#rsi-case/" + c.id));
        main.append(meta, title, el("p", c.objective, "rsi-objective"));
        const foot = el("div", undefined, "rsi-case-foot");
        foot.append(el("span", `${c.trials.length} 次尝试 · ${rsiBytes(c.exported_bytes)} · ${time(c.created_at)}`), link("查看证据 →", "#rsi-case/" + c.id, "rsi-text-link"));
        card.append(main, foot); body.append(card);
      }
    } else {
      const matches = data.candidates.filter(c => `${c.id} ${c.profile_id} ${c.parent_commit} ${rsiPhases[c.phase] || c.phase}`.toLowerCase().includes(query));
      const actions = el("div", undefined, "rsi-list-actions"), create = rsiButton("＋ 创建候选版本", () => rsiCandidateDialog(token, data), true);
      create.disabled = !data.profiles.length || !data.cases.some(c => c.status === "ready");
      actions.append(el("p", !data.profiles.length ? "在上方选择执行 Agent 并保存，即可创建候选。" : !data.cases.some(c => c.status === "ready") ? "先创建一份已冻结的诊断案例。" : "基于已冻结的证据，启动一次独立改进。"), create); body.append(actions);
      if (!matches.length) body.append(rsiEmpty(query ? "没有匹配的候选" : "还没有候选版本", query ? "换个关键词重试。" : "候选生成后，这里将展示检查结果、复评结论与草稿 PR。"));
      else body.append(table(["候选 / 配置", "父版本", "检查状态", "最近复评", "交付"], matches.map(c => {
        const name = el("div"); name.append(link(c.id.slice(0, 8) + " →", "#rsi/" + c.id, "rsi-text-link"), el("p", rsiExecutorNames[c.profile_id] || c.profile_id));
        return [name, c.parent_candidate ? link(c.parent_candidate.slice(0, 8), "#rsi/" + c.parent_candidate) : (c.parent_commit || "").slice(0, 10),
          rsiBadge(rsiPhases[c.phase] || c.phase, c.phase === "validated" ? "success" : ["failed", "interrupted"].includes(c.phase) ? "warning" : "neutral"),
          rsiAssessments[c.evaluations.at(-1)?.assessment] || "尚未复评", c.pr_url ? link("草稿 PR ↗", c.pr_url, "rsi-text-link") : "未发布"];
      })));
    }
  }
  for (const [name, label] of [["cases", `诊断案例 ${data.cases.length}`], ["candidates", `候选版本 ${data.candidates.length}`]]) {
    const button = rsiButton(label, () => { rsiUi.tab = name; draw(); }); tabs.append(button); tabButtons.push([name, button]);
  }
  search.oninput = () => { rsiUi.search = search.value; draw(); };
  toolbar.append(tabs, search); archive.append(toolbar, body); shell.append(archive); draw();
  if (data.actions.some(a => ["queued", "running", "failed", "interrupted"].includes(a.status))) {
    const activity = rsiPanel("最近操作", "查看执行进度或需要处理的异常。");
    activity.append(table(["操作", "候选", "状态", "更新时间"], data.actions.slice(0, 8).map(a => [rsiActionNames[a.kind] || a.kind,
      link(a.candidate.slice(0, 8), "#rsi/" + a.candidate, "rsi-text-link"), rsiPhases[a.status] || a.status, time(a.updated)])));
    shell.append(activity);
  }
}

function rsiDialog(title, description) {
  const dialog = el("dialog", undefined, "rsi-dialog"), header = el("div", undefined, "rsi-dialog-head"), text = el("div");
  const opener = document.activeElement;
  const heading = el("h2", title); heading.id = "rsi-dialog-title";
  dialog.setAttribute("aria-labelledby", heading.id);
  text.append(heading, el("p", description));
  const close = rsiButton("关闭", () => dialog.close());
  header.append(text, close); dialog.append(header);
  const form = el("form", undefined, "rsi-form"), error = el("p", undefined, "error"); error.setAttribute("role", "alert");
  dialog.append(form); view.append(dialog);
  dialog.addEventListener("close", () => { dialog.remove(); if (opener?.isConnected) opener.focus(); });
  const navigation = () => { dialog.close(); };
  window.addEventListener("hashchange", navigation, { once: true });
  dialog.addEventListener("close", () => window.removeEventListener("hashchange", navigation));
  dialog.showModal();
  return { dialog, form, error, close };
}

function rsiCaseDialog(token, experiments) {
  const { dialog, form, error, close } = rsiDialog("新建诊断案例", "冻结实验中的诊断材料，保留来源与改进目标。此步骤不调用模型。");
  const completed = experiments.runs.filter(r => !["queued", "running"].includes(r.status) && r.agents.length);
  if (!completed.length) {
    form.append(rsiEmpty("暂无可用的已结束实验", "当前工作空间没有已结束且包含 Agent 的实验。已有诊断案例仍可正常查阅。", link("前往实验记录 →", "#runs", "button primary")));
    return;
  }
  const run = rsiSelect(completed.map(r => [r.id, r.name || r.id]), "来源实验"), agent = rsiSelect([], "被改进 Agent");
  const updateAgents = () => agent.replaceChildren(...(completed.find(r => r.id === run.value)?.agents || []).map(a => {
    const option = el("option", a); option.value = a; return option;
  }));
  run.onchange = updateAgents; updateAgents();
  const scope = rsiSelect([["failed", "运行异常的尝试"], ["all", "该 Agent 的全部已记录尝试"]], "诊断范围");
  const objective = el("textarea"); objective.rows = 5; objective.maxLength = 16000; objective.required = true;
  objective.placeholder = "例如：根据工具调用与进程记录，定位 shell 超时后未清理子进程的原因，并补充回归测试。";
  const grid = el("div", undefined, "rsi-form-grid"); grid.append(rsiField("被改进 Agent", agent), rsiField("诊断范围", scope));
  const submit = rsiButton("冻结诊断材料", null, true); submit.type = "submit";
  form.append(rsiField("来源实验", run), grid, rsiField("改进目标", objective, "写清楚希望修复的问题，以及如何验证。"), error, submit);
  form.onsubmit = async event => {
    event.preventDefault(); if (submit.disabled) return;
    if (!objective.value.trim()) { error.textContent = "请填写具体的改进目标。"; objective.focus(); return; }
    const selected = { run_id: run.value, agent: agent.value, objective: objective.value.trim() }, all = scope.value === "all";
    submit.disabled = true; submit.textContent = "正在冻结证据…"; error.textContent = "";
    try {
      const detail = await api("/api/runs/" + selected.run_id);
      const trials = all ? detail.trials.filter(t => t.agent === selected.agent).map(t => t.trial) : null;
      if (all && !trials.length) throw Error("该 Agent 暂无已记录尝试，无法创建案例。");
      const result = await api("/api/rsi/cases", { method: "POST", body: JSON.stringify({ ...selected, trials }) });
      if (epoch === token && dialog.isConnected) { dialog.close(); location.hash = "#rsi-case/" + result.id; }
    } catch (err) { error.textContent = err.message; }
    finally { submit.disabled = false; submit.textContent = "冻结诊断材料"; }
  };
  close.focus();
}

function rsiCandidateDialog(token, data, caseId) {
  const { dialog, form, error } = rsiDialog("创建候选版本", "选择诊断案例与执行配置，在独立源码副本中运行改进。");
  if (!data.profiles.length || !data.cases.some(c => c.status === "ready")) {
    form.append(rsiEmpty("暂时无法创建候选", !data.profiles.length ? "请先配置执行 Agent，再刷新页面。" : "请先冻结一份诊断案例。")); return;
  }
  const source = rsiSelect(data.cases.filter(c => c.status === "ready").map(c => [c.id, `${c.agent} · ${rsiTitle(c)} · ${c.id.slice(0, 8)}`]), "诊断案例");
  if (caseId) source.value = caseId;
  const profile = rsiSelect(data.profiles.map(p => [p.id, p.name || `${p.id} · ${p.version}`]), "执行 Agent");
  const preferred = "builtin-" + data.executor_settings?.executor;
  if (data.profiles.some(p => p.id === preferred)) profile.value = preferred;
  const parent = rsiSelect([["", "配置中固定的源码版本"], ...data.candidates.filter(c => c.phase === "validated").map(c => [c.id, `${c.profile_id} · ${c.id.slice(0, 8)} · ${(c.commit || "").slice(0, 8)}`])], "父版本");
  const info = el("p", undefined, "rsi-inline-note");
  const update = () => { const p = data.profiles.find(p => p.id === profile.value); const c = data.cases.find(c => c.id === source.value); info.textContent = `被改进的 Agent：${c?.agent || "未知"}。${p.executor ? "执行模型：" + (data.executor_settings.models?.find(m => m.id === data.executor_settings.bindings?.["executor_" + p.executor])?.name || data.executor_settings.model || "未指定") + "。" : ""}父子复评：${p.evaluation ? "已配置" : "未配置"}。点击运行后将调用模型并产生费用。${p.executor && c?.agent !== "bkcrab" ? "当前内置检查仅检查补丁格式，不代表功能验证通过。" : ""}`; };
  source.onchange = update;
  profile.onchange = update; update();
  const submit = rsiButton("创建并运行改进", null, true); submit.type = "submit";
  form.append(rsiField("诊断案例", source), rsiField("执行 Agent", profile), rsiField("父版本", parent, "可从同一仓库中已通过检查的候选继续改进。"), info, error, submit);
  form.onsubmit = async event => {
    event.preventDefault(); if (submit.disabled) return;
    submit.disabled = true; submit.textContent = "正在创建候选…"; error.textContent = "";
    try {
      const result = await api("/api/rsi/candidates", { method: "POST", body: JSON.stringify({ case_id: source.value, profile: profile.value, parent: parent.value || null }) });
      let queueError = "";
      try { await api(`/api/rsi/candidates/${result.id}/actions`, { method: "POST", body: JSON.stringify({ kind: "improve" }) }); }
      catch (err) { queueError = err.message; }
      // Creation succeeded even if enqueueing failed. Open that candidate instead of creating a duplicate.
      if (epoch === token && dialog.isConnected) {
        dialog.close(); location.hash = "#rsi/" + result.id;
        if (queueError) window.addEventListener("hashchange", () => notice("候选已创建，但未能加入队列。可在详情页重新运行改进：" + queueError), { once: true });
      }
    } catch (err) { error.textContent = err.message; }
    finally { submit.disabled = false; submit.textContent = "创建并运行改进"; }
  };
}

function rsiEvidence(source) {
  const items = source.evidence || [], truncated = items.filter(e => e.truncated).length, missing = items.filter(e => e.error).length;
  const panel = rsiPanel("证据索引", "原始内容保存在本地证据包；这里展示来源、采集范围与材料完整性。");
  const summary = el("div", undefined, "rsi-evidence-summary");
  summary.append(rsiBadge(`${items.length} 项材料`), rsiBadge(`${truncated} 项截断`, truncated ? "warning" : "neutral"), rsiBadge(`${missing} 项缺失 / 读取失败`, missing ? "warning" : "neutral"));
  panel.append(summary);
  const state = rsiUi.evidence.get(source.id) || { query: "", category: "", status: "", page: 0 };
  rsiUi.evidence.set(source.id, state);
  const toolbar = el("div", undefined, "rsi-evidence-filters"), search = el("input");
  search.type = "search"; search.placeholder = "搜索文件路径或引用编号"; search.setAttribute("aria-label", "搜索证据"); search.value = state.query;
  const categories = [...new Set(items.map(e => e.category))];
  const category = rsiSelect([["", "全部来源"], ...categories.map(c => [c, rsiCategories[c] || c])], "证据来源"); category.value = state.category;
  const status = rsiSelect([["", "全部材料状态"], ["complete", "完整采集"], ["truncated", "内容截断"], ["missing", "缺失 / 读取失败"]], "材料状态"); status.value = state.status;
  const content = el("div"), pager = el("div", undefined, "rsi-pager");
  const matchesStatus = e => !state.status || (state.status === "missing" ? !!e.error : state.status === "truncated" ? !!e.truncated : !e.error && !e.truncated);
  function draw() {
    const matches = items.filter(e => (!state.category || e.category === state.category) && matchesStatus(e) && `${e.id || ""} ${e.source || ""} ${e.error || ""}`.toLowerCase().includes(state.query.toLowerCase()));
    const pages = Math.max(1, Math.ceil(matches.length / 20)); state.page = Math.max(0, Math.min(state.page, pages - 1));
    content.replaceChildren();
    if (!matches.length) content.append(rsiEmpty("没有匹配的材料", "调整关键词、来源或材料状态筛选。"));
    else content.append(table(["引用 / 来源", "原始文件", "采集状态与范围"], matches.slice(state.page * 20, (state.page + 1) * 20).map(e => {
      const ref = el("div"), path = el("div", undefined, "rsi-file"), coverage = el("div", undefined, "rsi-coverage");
      ref.append(el("strong", e.id || "—", "mono"), el("p", rsiCategories[e.category] || e.category));
      const name = (e.source || "未记录路径").split("/").at(-1);
      path.append(el("strong", name), el("div", e.source || "—", "mono rsi-file-path"));
      coverage.append(rsiBadge(e.error ? "读取失败 / 缺失" : e.truncated ? "内容截断" : "完整采集", e.error || e.truncated ? "warning" : "success"));
      if (e.error) {
        const detail = el("details"); detail.append(el("summary", "查看原因"), el("p", e.error)); coverage.append(detail);
      } else coverage.append(el("p", `${rsiBytes(e.bytes)} / ${rsiBytes(e.original_size)}`), el("small", `字节 ${number(e.offset)}–${number(e.offset + e.bytes)}`));
      return [ref, path, coverage];
    })));
    const prev = rsiButton("上一页", () => { state.page--; draw(); }), next = rsiButton("下一页", () => { state.page++; draw(); });
    prev.disabled = state.page === 0; next.disabled = state.page >= pages - 1;
    pager.replaceChildren(el("span", `${matches.length} 项 · 第 ${state.page + 1} / ${pages} 页`), prev, next);
  }
  for (const input of [search, category, status]) input.addEventListener(input === search ? "input" : "change", () => {
    state.query = search.value; state.category = category.value; state.status = status.value; state.page = 0; draw();
  });
  toolbar.append(search, category, status); panel.append(toolbar, content, pager); draw(); return panel;
}

async function renderRsiCase(token, identity) {
  const [source, overview] = await Promise.all([api("/api/rsi/cases/" + identity), api("/api/rsi")]);
  if (epoch !== token) return;
  const shell = rsiShell(); shell.append(link("← 返回改进档案", "#rsi", "rsi-back"));
  const create = rsiButton("从此案例创建候选", () => rsiCandidateDialog(token, overview, identity), true);
  create.disabled = !overview.profiles.length || source.status !== "ready";
  create.title = !overview.profiles.length ? "请先配置执行 Agent" : source.status !== "ready" ? "证据尚未冻结完成" : "";
  shell.append(rsiHeader(rsiTitle(source), `${source.agent} · 案例 ${identity.slice(0, 8)} · ${time(source.created_at)}`, [create]));
  const panel = rsiPanel("改进目标"); panel.append(el("p", source.objective, "rsi-goal"));
  const meta = el("div", undefined, "rsi-meta"); meta.append(rsiBadge(source.status === "ready" ? "证据已冻结" : "采集中", source.status === "ready" ? "success" : "warning"), el("span", `${source.trials.length} 次尝试 · ${rsiBytes(source.exported_bytes)} 已导出`));
  panel.append(meta);
  const origin = el("details"); origin.append(el("summary", "查看原始实验与尝试编号"), el("p", source.run || "未记录原始实验目录", "mono"));
  source.trials.forEach(t => origin.append(el("p", t, "mono")));
  if (source.source_run_id) origin.append(link("打开来源实验 →", "#run/" + source.source_run_id, "rsi-text-link"));
  panel.append(origin); shell.append(panel);
  const config = rsiConfigNote(overview); if (config) shell.append(config);
  shell.append(rsiEvidence(source));
}

async function renderRsiCandidate(token, identity, initial) {
  if (!initial && (rsiUi.pending || view.querySelector("dialog[open]"))) return;
  const [candidate, overview] = await Promise.all([api("/api/rsi/candidates/" + identity), api("/api/rsi")]);
  const source = await api("/api/rsi/cases/" + candidate.case);
  if (epoch !== token || (!initial && rsiUi.pending)) return;
  const actions = overview.actions.filter(a => a.candidate === identity), signature = JSON.stringify([candidate, actions]);
  if (!initial && view.dataset.rsiSignature === signature) return;
  view.dataset.rsiSignature = signature;
  const shell = rsiShell(); shell.append(link("← 返回改进档案", "#rsi", "rsi-back"));
  shell.append(rsiHeader("候选 " + identity.slice(0, 8), `改进目标 ${source.agent} · 执行 Agent ${rsiExecutorNames[candidate.profile_id] || candidate.profile_id} · ${time(candidate.created_at)}`,
    [link("查看诊断案例 →", "#rsi-case/" + candidate.case, "button")]));
  const status = el("div", undefined, "rsi-candidate-status");
  for (const [title, value, tone] of [["代码检查", rsiPhases[candidate.phase] || candidate.phase, candidate.phase === "validated" ? "success" : ["failed", "interrupted"].includes(candidate.phase) ? "warning" : "neutral"],
    ["效果验证", rsiAssessments[candidate.evaluations.at(-1)?.assessment] || "尚未复评", "neutral"], ["交付状态", candidate.pr_url ? "已创建草稿 PR" : "未发布", "neutral"]]) {
    const item = el("div"); item.append(el("small", title), rsiBadge(value, tone)); status.append(item);
  }
  shell.append(status);
  const panel = rsiPanel("下一步操作", "检查通过、评测改善和 PR 发布是三个独立状态。");
  const active = actions.find(a => ["queued", "running"].includes(a.status));
  if (active) panel.append(el("p", `${rsiActionNames[active.kind]} · ${rsiPhases[active.status]}，页面会自动更新。`, "rsi-inline-note"));
  const error = el("p", candidate.last_error || "", "error"); error.setAttribute("role", "alert");
  const controls = el("div", undefined, "rsi-action-grid"), buttons = [];
  const nextAction = candidate.phase === "created" ? "improve" : candidate.can_evaluate && !candidate.evaluations.length ? "evaluate" : candidate.can_publish && !candidate.pr_url ? "publish" : "evaluate";
  const confirm = el("input"); confirm.type = "checkbox"; confirm.id = "rsi-live";
  for (const [kind, label, desc] of [["improve", "运行改进", "根据诊断证据修改源码，并执行配置检查。"], ["evaluate", "运行父子复评", "在相同条件下评测父版本与候选。"], ["publish", "发布草稿 PR", "发布已检查的修改，交由人工审阅。"]]) {
    const required = kind === "improve" ? "created" : "validated";
    const reason = active ? "等待当前操作结束" : candidate.phase !== required ? (kind === "improve" ? "该候选已有改进记录" : "需要先通过代码检查") :
      kind === "evaluate" && !candidate.can_evaluate ? "执行配置中未指定复评模板" : kind === "publish" && !candidate.can_publish ? "执行配置中未指定 GitHub 仓库" : "";
    const card = el("div"), button = rsiButton(label, async () => {
      if (rsiUi.pending) return;
      rsiUi.pending = true; buttons.forEach(b => b.disabled = true); error.textContent = "";
      let enqueued = false;
      try {
        await api(`/api/rsi/candidates/${identity}/actions`, { method: "POST", body: JSON.stringify({ kind, confirm_unbounded: kind === "evaluate" && confirm.checked }) });
        enqueued = true;
        if (epoch === token) await renderRsiCandidate(token, identity, true);
      } catch (err) { if (epoch === token) {
        error.textContent = enqueued ? "操作已加入队列，状态刷新失败；请稍后刷新页面。" : err.message;
        if (!enqueued) buttons.forEach(b => b.disabled = b.dataset.blocked === "true");
      } }
      finally { rsiUi.pending = false; }
    }, kind === nextAction);
    button.disabled = !!reason; button.dataset.blocked = String(!!reason); buttons.push(button);
    card.append(button, el("p", reason || desc)); controls.append(card);
  }
  panel.append(controls);
  if (overview.allow_live && candidate.can_evaluate) panel.append(rsiField("若模板使用真实模型：确认本次父版本与候选两次复评不设费用上限", confirm));
  if (candidate.pr_url) panel.append(link("打开草稿 PR ↗", candidate.pr_url, "rsi-text-link"));
  panel.append(error); shell.append(panel);
  const diagnosis = rsiPanel("诊断与修改", source.objective);
  if (candidate.diagnosis) {
    for (const [title, value] of [["诊断结论", candidate.diagnosis.summary], ["代码修改", candidate.diagnosis.changes], ["待验证假设", (candidate.diagnosis.hypotheses || []).join("\n")], ["适用范围与限制", candidate.diagnosis.limitations || "未声明额外限制"]]) {
      if (value) diagnosis.append(el("h3", title), el("p", value, "rsi-prose"));
    }
    diagnosis.append(el("p", "证据引用：" + candidate.diagnosis.evidence_refs.join("、"), "mono"));
  } else diagnosis.append(el("p", "完成改进后，这里将显示诊断结论、源码修改与引用证据。", "rsi-inline-note"));
  const versions = el("details"); versions.append(el("summary", "源码版本与可追溯性"), el("p", `父提交：${candidate.parent_commit}`, "mono"), el("p", `候选提交：${candidate.commit || "尚未生成"}`, "mono"));
  if (candidate.parent_candidate) versions.append(link("查看父候选 →", "#rsi/" + candidate.parent_candidate, "rsi-text-link"));
  versions.append(el("p", candidate.matches_measured_clean_commit === true ? "父提交与测评记录中的干净源码提交一致。" : "尚不能确认父提交等同于历史被测源码；复评会重新构建父版本与候选。"));
  if (candidate.source_checkout_dirty_ignored) versions.append(el("p", "原工作区有未提交改动；本候选仅使用固定的 Git 提交。"));
  diagnosis.append(versions); shell.append(diagnosis);
  const validation = rsiPanel("检查与复评", "检查结果与配对评测分别记录，不将未验证的修改标为效果改善。");
  if (rsiExecutorNames[candidate.profile_id]) validation.append(el("p", source.agent === "bkcrab" ? "内置代码检查：Go 测试（go test ./...）。" : "内置代码检查仅验证补丁格式，不代表功能测试或模型效果验证通过。", "rsi-inline-note"));
  if (candidate.checks?.length) validation.append(table(["代码检查", "结果", "完成时间"], candidate.checks.map(c => ["检查 " + (c.index + 1), c.returncode === 0 ? rsiBadge("通过", "success") : rsiBadge("退出码 " + c.returncode, "warning"), time(c.finished_at)])));
  else validation.append(el("p", "暂无代码检查记录。", "rsi-inline-note"));
  if (candidate.evaluations.length) validation.append(table(["复评", "结论", "父分数", "候选分数", "异常（父 / 候选）", "可靠性 / 限制"], candidate.evaluations.map(e => [e.id.slice(0, 8), rsiAssessments[e.assessment] || e.assessment,
    number(e.scores?.parent), number(e.scores?.candidate), e.errors ? `${e.errors.parent} / ${e.errors.candidate}` : "未知",
    `${({ improved: "异常减少", regressed: "异常增加", equal: "异常数相同" })[e.reliability_change] || "未比较"}；${(e.issues || [e.error || ""]).join("；")}`])));
  else validation.append(el("p", "尚未运行父子复评，性能提升未验证。", "rsi-inline-note"));
  shell.append(validation);
  if (actions.length) {
    const history = rsiPanel("操作记录"); history.append(table(["操作", "状态", "更新时间", "异常说明"], actions.map(a => [rsiActionNames[a.kind] || a.kind, rsiPhases[a.status] || a.status, time(a.updated), a.error || "—"]))); shell.append(history);
  }
  for (const [title, text] of [["候选补丁（最多展示 64 KiB）", candidate.patch], ["PR 正文", candidate.pr_body]]) {
    if (!text) continue;
    const section = el("details", undefined, "panel rsi-panel rsi-code"); section.append(el("summary", title), el("pre", text)); shell.append(section);
  }
  shell.append(rsiEvidence(source));
}
