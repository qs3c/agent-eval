"use strict";

// Entry preference is local to this browser. Preparation always comes from the API.
let homePreferred = false;
let guideInProgress = false;
const homePreferenceKey = "agent-harness-eval.home.v1";
const landingSources = [
  ["runs", "/api/runs", "实验记录"],
  ["catalog", "/api/integrations", "Agent 版本"],
  ["pool", "/api/models", "模型配置"],
  ["rsi", "/api/rsi", "改进档案"],
];

async function loadLandingData() {
  const results = await Promise.allSettled(landingSources.map(([, path]) => api(path)));
  const data = { errors: [] };
  results.forEach((result, i) => {
    const [key, , label] = landingSources[i];
    if (result.status === "fulfilled") data[key] = result.value;
    else data.errors.push(label);
  });
  return data;
}

function preferHome() {
  homePreferred = true;
  try { localStorage.setItem(homePreferenceKey, "1"); } catch { /* Storage may be disabled. */ }
}

function landingEntry(data) {
  let preferred = homePreferred;
  try { preferred ||= localStorage.getItem(homePreferenceKey) === "1"; } catch { /* Use session preference. */ }
  const hasWork = data.runs?.runs?.length || data.catalog?.releases?.length || data.pool?.models?.length ||
    data.rsi?.cases?.length || data.rsi?.candidates?.length;
  // Missing data is not evidence that a workspace is empty.
  return preferred || hasWork || data.errors.length ? "home" : "welcome";
}

function updateGuideReturn(page) {
  if (page === "welcome") guideInProgress = true;
  if (page === "home") guideInProgress = false;
  const banner = $("#guide-return");
  if (!banner) return;
  banner.hidden = !guideInProgress || !["integrations", "models", "setup", "run", "create"].includes(page);
  banner.replaceChildren(el("span", "首次使用 · 完成当前操作后，可返回引导查看下一步。"), link("返回使用引导 →", "#welcome"));
}

function landingHeading(eyebrow, title, description) {
  const block = el("div", undefined, "landing-heading");
  block.append(el("div", eyebrow, "eyebrow"), el("h1", title), el("p", description));
  return block;
}

function landingSection(title, description, href, action) {
  const section = el("section", undefined, "landing-panel");
  const head = el("div", undefined, "landing-panel-head"), text = el("div");
  text.append(el("h2", title));
  if (description) text.append(el("p", description));
  head.append(text);
  if (href) head.append(link(action, href, "landing-text-link"));
  section.append(head);
  return section;
}

function landingErrors(shell, data, retry) {
  if (!data.errors.length && !data.runs?.blocked) return;
  const box = el("div", undefined, "landing-warning");
  box.setAttribute("role", "status");
  box.append(el("p", data.errors.length ? `${data.errors.join("、")}暂时无法读取，相关状态显示为未知。` : data.runs.blocked));
  const button = el("button", "重新加载", "button");
  button.onclick = retry;
  box.append(button); shell.append(box);
}

function preparation(data) {
  const releases = data.catalog?.releases;
  const models = data.pool?.models;
  const records = data.runs?.runs;
  return [
    { title: "准备 Agent", detail: "接入自己的 Agent，或安装一个开源基线。构建完成后，先用控制任务检查运行链路。", href: "#integrations", action: "接入 Agent", done: !!releases?.length,
      state: !releases ? "状态未知" : releases.length ? `${releases.length} 个已构建版本` : "待接入", optional: false },
    { title: "配置评测模型", detail: "添加模型与凭据，用于真实能力评测。想先熟悉流程，可以跳过这步，使用模拟模型。", href: "#models", action: "配置模型", done: !!models?.some(m => m.has_api_key),
      state: !models ? "状态未知" : models.some(m => m.has_api_key) ? "已配置凭据 · 连通性待验证" : "模拟评测可跳过", optional: true },
    { title: "创建第一次评测", detail: "选择 Agent 版本和任务，预检后启动。建议先跑 controls，再开始真实模型对比。", href: "#setup", action: "创建评测", done: !!records?.length,
      state: !records ? "状态未知" : records.length ? "已有实验记录" : "待创建", optional: false },
  ];
}

function welcomeIllustration() {
  const art = el("div", undefined, "welcome-art");
  art.setAttribute("role", "img");
  art.setAttribute("aria-label", "工作流程示意：在同一组任务下评测不同 Agent，比较结果，追溯证据，再验证改进。");
  const inner = el("div", undefined, "welcome-art-inner");
  inner.setAttribute("aria-hidden", "true");
  inner.append(el("div", "从一次公平的对照开始", "art-caption"));
  const input = el("div", undefined, "art-task");
  input.append(el("span", "▤", "art-symbol"), el("strong", "同一组任务"), el("small", "固定条件 · 保留证据"));
  const branches = el("div", undefined, "art-branches");
  for (const [mark, title, detail] of [["A", "你的 Agent", "原生执行"], ["B", "开源基线", "对照评测"]]) {
    const branch = el("div", undefined, "art-agent");
    branch.append(el("span", mark, "art-agent-mark"), el("strong", title), el("small", detail)); branches.append(branch);
  }
  const output = el("div", undefined, "art-result");
  output.append(el("span", "↗", "art-symbol"), el("strong", "发现差异，验证改进"), el("small", "结果对比 / 执行轨迹 / RSI"));
  inner.append(input, branches, output, el("span", "工作流程示意", "art-footnote"));
  art.append(inner); return art;
}

async function renderWelcome(token, initial) {
  view.replaceChildren(el("p", "正在读取准备状态…", "landing-loading"));
  const data = initial || await loadLandingData();
  if (epoch !== token) return;
  const shell = el("div", undefined, "welcome-page landing-page");
  const top = el("div", undefined, "welcome-top");
  const skip = link("先进入工作台 ↗", "#home", "landing-text-link");
  skip.onclick = preferHome;
  top.append(el("span", "开始使用 / GETTING STARTED", "landing-kicker"), skip);
  const hero = el("section", undefined, "welcome-hero"), introduction = el("div", undefined, "welcome-intro");
  const heading = landingHeading("AGENT HARNESS EVAL", "从了解你的 Agent，\n到验证它的进步。", "在相同任务下比较表现，沿着执行证据找到差异，再用一次评测验证改进。让每一个结论，都有来处。");
  const steps = preparation(data), next = steps.find(s => !s.done && !s.optional);
  const actions = el("div", undefined, "landing-actions");
  actions.append(link(next ? next.action + " →" : "查看实验结果 →", next?.href || "#runs", "button primary"));
  const learn = el("button", "查看准备步骤 ↓", "landing-text-button");
  learn.onclick = () => {
    const target = $("#welcome-steps");
    target.focus({preventScroll: true});
    target.scrollIntoView({behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth", block: "start"});
  };
  actions.append(learn);
  introduction.append(heading, actions, el("p", "从模拟评测开始，无需先配置 API Key。", "welcome-note"));
  hero.append(introduction, welcomeIllustration());
  shell.append(top, hero);
  landingErrors(shell, data, () => renderWelcome(token));
  const guide = el("section", undefined, "welcome-preparation");
  guide.id = "welcome-steps"; guide.tabIndex = -1;
  const guideHead = el("div", undefined, "welcome-preparation-head");
  guideHead.append(el("div", "YOUR FIRST EXPERIMENT", "eyebrow"), el("h2", "准备好，开始第一次评测"), el("p", "按自己的节奏完成准备，随时可以离开或回来继续。"));
  const list = el("ol", undefined, "welcome-steps");
  steps.forEach((step, i) => {
    const row = el("li", undefined, "welcome-step" + (step.done ? " is-ready" : ""));
    const text = el("div", undefined, "welcome-step-copy");
    const title = el("div", undefined, "welcome-step-title");
    title.append(el("h3", step.title), el("span", step.state, "landing-chip" + (step.done ? " ready" : "")));
    text.append(title, el("p", step.detail));
    row.append(el("span", String(i + 1).padStart(2, "0"), "welcome-step-number"), text, link(step.done ? "查看配置 ↗" : step.action + " →", step.href, "landing-text-link"));
    if (i === 2 && step.done) row.lastChild.replaceWith(link("查看实验 ↗", "#runs", "landing-text-link"));
    list.append(row);
  });
  guide.append(guideHead, list); shell.append(guide);
  const after = el("section", undefined, "welcome-after");
  after.append(el("h2", "第一次评测，只是开始。"));
  const items = el("div", undefined, "welcome-outcomes");
  for (const [title, description] of [["对比表现", "在相同任务下，发现不同 Agent 的优势与不足。"], ["追溯过程", "从分数回到原生轨迹，理解每次尝试发生了什么。"], ["验证改进", "冻结问题证据，生成候选版本，再用复评检验效果。"]]) {
    const item = el("div"); item.append(el("h3", title), el("p", description)); items.append(item);
  }
  after.append(items); shell.append(after); view.replaceChildren(shell);
}

function sortedHomeRuns(data) {
  return [...(data.runs?.runs || [])].sort((a, b) => (Date.parse(b.created) || 0) - (Date.parse(a.created) || 0));
}
function homeRunNeedsAttention(run) {
  return ["failed", "interrupted", "incomplete", "held"].includes(run.status) ||
    (run.execution_summary?.finished && (run.execution_summary.errors > 0 || run.execution_summary.unscored > 0));
}

function homeRunRow(run) {
  const row = el("article", undefined, "home-run-row"), text = el("div", undefined, "home-run-copy");
  text.append(link(run.name, "#run/" + encodeURIComponent(run.id), "home-run-name"), el("p", `${run.benchmark} · ${(run.agents || []).join(" / ")} · ${modes[run.mode] || run.mode}`));
  const state = el("div", undefined, "home-run-state");
  state.append(runState(run), el("small", time(run.created)));
  row.append(text, state); return row;
}

async function renderHome(token, initial) {
  view.replaceChildren(el("p", "正在读取工作空间…", "landing-loading"));
  const data = initial || await loadLandingData();
  if (epoch !== token) return;
  const records = sortedHomeRuns(data);
  const active = records.filter(r => ["running", "queued", "pausing", "cancelling"].includes(r.status));
  const attention = records.filter(homeRunNeedsAttention);
  const shell = el("div", undefined, "home-page landing-page");
  const header = el("section", undefined, "home-header");
  const actions = el("div", undefined, "landing-actions");
  const refresh = el("button", "刷新状态", "button");
  refresh.onclick = () => renderHome(token);
  actions.append(refresh, link("＋ 创建对比", "#setup", "button primary"));
  header.append(landingHeading("YOUR WORKSPACE", "把每次实验，变成下一步的依据。", "查看正在发生的工作，继续最近的实验与改进。"), actions);
  shell.append(header); landingErrors(shell, data, () => renderHome(token));
  const grid = el("div", undefined, "home-grid"), focus = el("section", undefined, "home-focus");
  const current = active[0] || records[0];
  focus.append(el("div", active.length ? "IN PROGRESS / 正在进行" : current ? "PICK UP WHERE YOU LEFT OFF / 继续工作" : "YOUR NEXT STEP / 下一步", "eyebrow"));
  if (current) {
    focus.append(el("h2", current.name), el("p", `${current.benchmark} · ${(current.agents || []).join(" / ")}`));
    const state = el("div", undefined, "home-focus-state"); state.append(runState(current));
    if (active.length) state.append(el("span", `尝试结束 ${number(current.completed)} / ${number(current.expected)}`));
    focus.append(state, link(active.length ? "查看执行进度 →" : "继续查看实验 →", "#run/" + encodeURIComponent(current.id), "button primary"));
  } else {
    const ready = !!data.catalog?.releases?.length;
    focus.append(el("h2", data.runs ? "从一次小规模评测开始" : "实验状态暂时不可用"), el("p", data.runs ? "先选少量任务，跑通评测，再逐步扩大范围。你的实验和证据会保留在这里。" : "可以先查看 Agent 与模型配置，或重新加载工作空间。"));
    focus.append(link(ready ? "创建第一次评测 →" : "准备 Agent →", ready ? "#setup" : "#integrations", "button primary"), link("查看使用引导", "#welcome", "landing-text-link"));
  }
  const workspace = landingSection("工作空间", "开始新实验前，确认可用配置。");
  for (const [title, value, href] of [["Agent 版本", data.catalog ? `${data.catalog.releases.length} 个已构建` : "状态未知", "#integrations"], ["模型凭据", data.pool ? `${data.pool.models.filter(m => m.has_api_key).length} 个已配置` : "状态未知", "#models"]]) {
    const item = link("", href, "home-resource"); item.append(el("span", title), el("strong", value), el("span", "↗")); workspace.append(item);
  }
  workspace.append(el("p", "已构建不代表运行检查通过；模型可用性以实际请求为准。", "home-resource-note"));
  grid.append(focus, workspace); shell.append(grid);
  const recent = landingSection("最近实验", "从上次停下的地方继续。", "#runs", "全部实验 →");
  if (!data.runs) recent.append(el("p", "实验记录暂时无法读取。", "landing-empty"));
  else if (!records.length) recent.append(el("p", "还没有实验。创建第一次评测后，这里会展示最近的进展。", "landing-empty"));
  else records.slice(0, 4).forEach(run => recent.append(homeRunRow(run)));
  shell.append(recent);
  const bottom = el("div", undefined, "home-grid home-bottom");
  const needs = landingSection("需要关注", "执行异常与缺失评分单独保留。", "#runs", "查看记录 →");
  if (!data.runs) needs.append(el("p", "暂时无法确认实验状态。", "landing-empty"));
  else if (!attention.length) needs.append(el("p", records.length ? "当前没有需要关注的执行异常。" : "有实验后，这里会提示执行异常和缺失评分。", "landing-empty"));
  else attention.slice(0, 3).forEach(run => needs.append(homeRunRow(run)));
  const improvement = landingSection("改进档案", "从问题证据，到可验证的候选版本。", "#rsi", "进入 RSI →");
  if (!data.rsi) improvement.append(el("p", "改进档案暂时无法读取。", "landing-empty"));
  else if (!data.rsi.cases.length && !data.rsi.candidates.length) improvement.append(el("p", "遇到值得研究的失败后，从实验中选择测试记录，开始第一次诊断。", "landing-empty"));
  else {
    const summary = el("p", `${data.rsi.cases.length} 份诊断案例 · ${data.rsi.candidates.length} 个候选版本`, "home-rsi-summary");
    improvement.append(summary);
    data.rsi.cases.slice(-3).reverse().forEach(item => {
      const entry = link("", "#rsi-case/" + encodeURIComponent(item.id), "home-case");
      const copy = el("div");
      copy.append(el("span", rsiTitle(item)));
      if (item.diagnosis_topic) copy.append(el("p", item.diagnosis_topic));
      entry.append(copy, el("span", "↗")); improvement.append(entry);
    });
  }
  bottom.append(needs, improvement); shell.append(bottom);
  const foot = el("div", undefined, "home-footnote");
  foot.append(el("span", `读取于 ${new Date().toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"})} · 可刷新查看最新进展`), link("重新查看使用引导 ↗", "#welcome", "landing-text-link"));
  shell.append(foot); view.replaceChildren(shell);
}
