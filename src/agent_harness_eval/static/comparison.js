"use strict";
function datasetLabel(dataset) {
  return dataset
    ? `${dataset.name} · ${dataset.split || "split 未记录"} · 修订 ${dataset.revision || "未记录"}`
    : "数据集名称 / 修订 / split 未记录";
}
function comparisonScore(value, kind) {
  return value == null ? "待评分" : kind === "resolved" ? `${number(value * 100)}%` : number(value);
}
function taskScore(cell, kind) {
  const assistance = cell.assisted ? ` · 含 ${cell.assisted} 次辅助` : "";
  if (cell.score != null) {
    if (kind === "resolved" && cell.expected === 1) return (cell.score === 1 ? "已解决" : "未解决") + assistance;
    return comparisonScore(cell.score, kind) + assistance;
  }
  return `待完整评分 · ${cell.scored}/${cell.expected} 已评分` +
    (cell.errors ? ` · ${cell.errors} 异常` : "") + (cell.extra ? ` · ${cell.extra} 额外记录` : "") + assistance;
}
function usageLabel(usage) {
  return usage.total != null ? number(usage.total) :
    `未知${usage.known != null ? `（已知部分 ${number(usage.known)}；${usage.recorded}/${usage.expected} 次）` : ""}`;
}
function pairCategory(a, b, binary) {
  if (a.score == null || b.score == null) return "incomplete";
  if (binary) return a.score === 1 ? (b.score === 1 ? "both" : "left") : (b.score === 1 ? "right" : "neither");
  return a.score > b.score ? "left" : a.score < b.score ? "right" : "equal";
}
async function renderExperimentCompare(token, requestedRun) {
  await refreshRuns();
  if (epoch !== token) return;
  const experiments = runs.filter(run => run.mode === "live");
  view.replaceChildren();
  const head = el("section", undefined, "page-head"), heading = el("div");
  heading.append(el("div", "AGENT COMPARISON", "eyebrow"), el("h1", "先看整体，再看每一道题。"),
    el("p", "选择一个实验，比较各 Agent 在同一组任务上的表现。"));
  head.append(heading, link("跨实验批次对比", "#batch-compare", "button"));
  const selector = el("div", undefined, "panel compare-form"), label = el("label", "实验"), select = el("select");
  select.setAttribute("aria-label", "选择对比实验");
  for (const run of experiments) {
    const option = el("option", `${run.name} · ${run.dataset?.name || run.benchmark}${run.dataset?.revision ? ` @${run.dataset.revision}` : ""} · ${run.agents.length} 个 Agent · ${modes[run.mode]} · ${time(run.created)}`);
    option.value = run.id;
    select.append(option);
  }
  label.append(select);
  selector.append(label);
  const body = el("div");
  body.setAttribute("aria-live", "polite");
  view.append(head, selector, body);
  if (!experiments.length) {
    body.append(el("p", "尚无真实模型实验记录。完成真实实验后，可在这里查看任务表现。", "empty"));
    select.disabled = true;
    return;
  }
  const preferred = experiments.find(r => r.id === requestedRun) || experiments[0];
  if (requestedRun && !experiments.some(r => r.id === requestedRun)) notice("指定实验不是真实模型实验或不存在，已选择最新真实实验。");
  select.value = preferred.id;
  let version = 0;
  async function load() {
    const v = ++version, id = select.value;
    body.replaceChildren(el("p", "正在读取本实验的任务结果…", "empty"));
    try {
      const data = await api(`/api/runs/${encodeURIComponent(id)}/comparison`);
      if (epoch !== token || v !== version) return;
      body.replaceChildren();
      drawExperimentComparison(data, body, () => epoch === token && v === version);
    } catch (error) {
      if (epoch === token && v === version) body.replaceChildren(el("p", error.message, "error"));
    }
  }
  select.onchange = () => {
    history.replaceState(null, "", `#compare/${select.value}`);
    load();
  };
  const refresh = el("button", "刷新结果", "button");
  refresh.type = "button";
  refresh.onclick = () => { ++version; renderExperimentCompare(token, select.value); };
  selector.append(refresh);
  await load();
}
function drawExperimentComparison(data, body, active) {
  const {run, benchmark, agents, tasks, attempts, score_kind: kind} = data;
  const names = Object.keys(agents), taskNames = Object.keys(tasks);
  const meta = el("section", undefined, "panel comparison-panel");
  meta.append(el("h2", `${run.name} · ${modes[run.mode]} · ${labels[run.status]}`), el("p", benchmark.id),
    el("p", datasetLabel(benchmark.dataset)),
    el("p", `本次选取 ${data.task_count} 题 · 每题 ${attempts} 次 · ${names.length} 个 Agent。成绩仅覆盖本次选题，不代表完整数据集成绩。`),
    fold("评分来源、版本与任务内容哈希", `${run.grading}。\n${benchmark.evaluation ? `评分器：${benchmark.evaluation.name} · 版本 ${benchmark.evaluation.version || "未记录"} · 环境 ${benchmark.evaluation.environment || "未记录"}` : "评分器 / 环境版本未记录。"}\n${JSON.stringify(benchmark.task_hashes, null, 2)}`));
  if (run.mode !== "live") meta.append(el("p", "平台自测 / 对照数据，不代表真实模型能力。", "callout warning"));
  if (names.some(name => run.reviews[name]?.status === "diagnostic")) meta.append(el("p", "本实验含诊断记录，下表保留其原始观测，不作为有效评测成绩。", "callout warning"));
  meta.append(fold("查看各 Agent 的复核依据", names.map(name => `${name} · ${reviewNames[run.reviews[name]?.status] || "待复核"}：${run.reviews[name]?.reason || "暂无复核记录"}`).join("\n")));
  if (data.unexpected.length) meta.append(el("p", "存在不属于计划的任务或 Agent 记录，暂不生成综合成绩。", "callout warning"));
  const summary = el("section", undefined, "panel comparison-panel");
  summary.append(el("h2", "整体表现 · 本次全部任务"),
    el("p", kind === "resolved" ? "主分 = 每题成功比例的等权平均。每题仅执行一次时，就是解决题数 ÷ 本次题数。" : `主分 = 每题 ${data.primary_metric} 的平均值，再对任务等权平均；保留原始单位，不转成解决率。`));
  summary.append(table(
    ["Agent", kind === "resolved" ? "解决率" : `主指标 ${data.primary_metric}`, attempts === 1 ? "解决题数" : "全次数通过题数", "评分覆盖", "缺失 / 异常 / 未评分 / 额外", "Agent 总秒数", "输入 Token", "输出 Token", "费用 USD"],
    names.map(name => {
      const a = agents[name];
      const score = a.final ? comparisonScore(a.score, kind) : a.provisional_score != null ? `${comparisonScore(a.provisional_score, kind)}（暂定）` : "待完整评分";
      const label = el("span", name);
      if (a.assisted) label.append(el("small", `${a.assisted} 次外部辅助；评分含辅助结果`, "run-sub"));
      label.append(el("small", reviewNames[run.reviews[name]?.status] || "待复核", "run-sub"));
      return [label, score + (run.reviews[name]?.status === "diagnostic" ? "（诊断）" : ""), kind === "resolved" ? `${a.solved_tasks} / ${data.task_count}` : "不适用",
        `${a.scored} / ${a.expected}`, `${a.missing} / ${a.errors} / ${a.unscored} / ${a.extra}`,
        ...["agent_seconds", "input_tokens", "output_tokens", "cost_usd"].map(k =>
          k.endsWith("tokens") ? tokenUsageLabel(a.usage[k].total, a.usage[k]) : usageLabel(a.usage[k]))];
    })));
  summary.append(el("p", "暂定比例 = 已观测成功次数 ÷ 计划次数；未评分并未判失败，完成后才形成最终主分。异常包含在未评分中。用量来自运行器原始结果，轨迹页会独立核对请求完整性；未知总量保留未知。总耗时为各次 Agent 执行时间之和。筛选下方任务不会改变本表。", "muted"));
  body.append(meta, summary);

  const matrix = el("section", undefined, "panel comparison-panel"), controls = el("div", undefined, "comparison-controls");
  matrix.append(el("h2", "逐题对比"), el("p", "选择两个不同 Agent，筛选差异题目；点击任务查看对应尝试的证据。"));
  const picks = ["Agent A", "Agent B"].map((text, i) => {
    const label = el("label", text), select = el("select");
    select.setAttribute("aria-label", text);
    names.forEach(name => {const option = el("option", name); option.value = name; select.append(option);});
    select.value = names[Math.min(i, names.length - 1)];
    label.append(select); controls.append(label); return select;
  });
  if (names.length < 2) {
    picks[1].disabled = true;
    matrix.append(el("p", "本实验只有一个 Agent，可以查看成绩和单条轨迹；两两对比需要实验包含两个 Agent。", "callout"));
  }
  const filterLabel = el("label", "任务筛选"), filter = el("select");
  filter.setAttribute("aria-label", "任务筛选"); filterLabel.append(filter); controls.append(filterLabel);
  const matrixBody = el("div");
  matrix.append(controls, matrixBody); body.append(matrix);
  const detail = el("section", undefined, "panel comparison-panel"), taskLabel = el("label", "查看任务"), taskSelect = el("select");
  taskSelect.setAttribute("aria-label", "查看任务");
  taskNames.forEach(task => {const op = el("option", task); op.value = task; taskSelect.append(op);});
  taskLabel.append(taskSelect);
  const evidence = el("div"); detail.append(el("h2", "单题结果与执行证据"), taskLabel, evidence); body.append(detail);
  let detailVersion = 0;
  function drawEvidence() {
    const v = ++detailVersion, task = taskSelect.value;
    evidence.replaceChildren();
    if (!task) { evidence.append(el("p", "当前筛选下没有任务。")); return; }
    const chosen = names.length === 1 ? [picks[0].value] : picks.map(s => s.value);
    if (new Set(chosen).size !== chosen.length) {evidence.append(el("p", "请选择两个不同的 Agent。", "callout warning")); return;}
    evidence.append(table(["Agent", "本题结果", "已评分 / 计划"], chosen.map(name => [name, taskScore(tasks[task][name], kind), `${tasks[task][name].scored} / ${attempts}`])));
    const trialControls = el("div", undefined, "comparison-controls"), columns = el("div", undefined, "compare-columns trace-columns");
    evidence.append(trialControls, columns);
    for (const name of chosen) {
      const records = tasks[task][name].trials, label = el("label", `${name} · 选择尝试`), select = el("select"), column = el("div");
      select.setAttribute("aria-label", `${name} 的尝试`);
      records.forEach((record, i) => {const op = el("option", `第 ${i + 1} 条记录 · ${record.trial}`); op.value = record.id; select.append(op);});
      label.append(select); trialControls.append(label); columns.append(column);
      let request = 0;
      async function loadTrace() {
        const ticket = ++request;
        column.replaceChildren(el("p", "正在读取任务轨迹…"));
        try {
          const t = await api(`/api/runs/${run.id}/traces/${select.value}`);
          if (!active() || v !== detailVersion || ticket !== request) return;
          if (t.task !== task || t.agent !== name) throw Error("轨迹的 Agent 或任务不匹配，已停止展示。");
          column.replaceChildren(traceCard(t));
        } catch (error) {
          if (active() && v === detailVersion && ticket === request) column.replaceChildren(el("p", error.message, "error"));
        }
      }
      select.onchange = loadTrace;
      if (records.length) loadTrace();
      else {select.disabled = true; column.append(el("p", `${name} 尚无已落盘的终态记录。`, "empty"));}
    }
  }
  function drawMatrix() {
    const left = picks[0].value, right = picks[1].value, binary = kind === "resolved" && attempts === 1;
    const selected = names.length === 1 ? [left] : [left, right];
    if (names.length > 1 && left === right) {
      matrixBody.replaceChildren(el("p", "请选择两个不同的 Agent。", "callout warning"));
      taskSelect.disabled = true; drawEvidence(); return;
    }
    const matching = taskNames.filter(task => filter.value === "all" || pairCategory(tasks[task][left], tasks[task][right], binary) === filter.value);
    matrixBody.replaceChildren(el("p", `显示 ${matching.length} / ${data.task_count} 题`), table(["任务", ...selected], matching.map(task => {
      const button = el("button", task, "button evidence-link"); button.type = "button";
      button.onclick = () => {taskSelect.value = task; drawEvidence(); detail.scrollIntoView({block: "start"});};
      return [button, ...selected.map(name => taskScore(tasks[task][name], kind))];
    })));
    const previous = taskSelect.value;
    taskSelect.replaceChildren(...matching.map(task => {const op = el("option", task); op.value = task; return op;}));
    if (matching.includes(previous)) taskSelect.value = previous;
    taskSelect.disabled = !matching.length;
    drawEvidence();
  }
  function updatePair() {
    const left = picks[0].value, right = picks[1].value, binary = kind === "resolved" && attempts === 1;
    const choices = names.length === 1 ? [["all", "全部任务"]] : binary ? [["all", "全部任务"], ["left", `仅 ${left} 解决`], ["right", `仅 ${right} 解决`], ["both", "都解决"], ["neither", "都未解决"], ["incomplete", "未完整评分"]] : [["all", "全部任务"], ["left", `${left} 数值较高`], ["right", `${right} 数值较高`], ["equal", "数值相同"], ["incomplete", "未完整评分"]];
    filter.replaceChildren(...choices.map(([value, text]) => {
      const count = value === "all" ? taskNames.length : taskNames.filter(t => pairCategory(tasks[t][left], tasks[t][right], binary) === value).length;
      const op = el("option", `${text} (${count})`); op.value = value; return op;
    }));
    drawMatrix();
  }
  picks.forEach(select => select.onchange = updatePair);
  filter.onchange = drawMatrix; taskSelect.onchange = drawEvidence;
  updatePair();
}
