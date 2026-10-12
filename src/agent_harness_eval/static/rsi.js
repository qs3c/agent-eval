"use strict";
const rsiPhases = { created: "待改进", improving: "分析与修改中", checking: "检查中", validated: "检查通过",
  failed: "改进失败", interrupted: "已中断", queued: "排队中", running: "执行中", completed: "已完成" };
const rsiAssessments = { improved: "本组评测改善", equal: "本组评测持平", regressed: "本组评测下降", inconclusive: "证据不足以比较", failed: "复评执行失败", running: "复评中" };

function rsiSelect(items, label) {
  const select = el("select");
  for (const [value, text] of items) {
    const option = el("option", text); option.value = value; select.append(option);
  }
  select.setAttribute("aria-label", label);
  return select;
}
function rsiField(label, input) {
  const wrapper = el("label", label); wrapper.append(input); return wrapper;
}
async function renderRsi(token, identity, initial = true) {
  if (identity) return renderRsiCandidate(token, identity, initial);
  const [data, experiments] = await Promise.all([api("/api/rsi"), api("/api/runs")]);
  if (epoch !== token) return;
  view.replaceChildren();
  const head = el("section", undefined, "page-head"), title = el("div");
  title.append(el("div", "RECURSIVE SELF-IMPROVEMENT", "eyebrow"), el("h1", "从测评证据到下一版 Agent。"),
    el("p", "诊断、修改、检查、复评与 PR 分别留证；候选按父版本持续积累。"));
  const refresh = el("button", "刷新记录", "button"); refresh.onclick = () => renderRsi(token);
  head.append(title, refresh); view.append(head);
  const counts = el("section", undefined, "stats");
  stats([["诊断案例", data.cases.length, "保存来源与缺失信息"], ["候选版本", data.candidates.length, "包含失败和未改善版本"],
    ["执行中 / 排队", data.actions.filter(a => ["queued", "running"].includes(a.status)).length, "由实验队列统一调度"]], counts);
  view.append(counts);
  const cases = el("section", undefined, "panel form-panel");
  cases.append(el("h2", "1. 从实验创建诊断案例"));
  const completed = experiments.runs.filter(r => !["queued", "running"].includes(r.status));
  const run = rsiSelect(completed.map(r => [r.id, r.name || r.id]), "来源实验");
  const agent = rsiSelect([], "被改进 Agent");
  const updateAgents = () => {
    agent.replaceChildren(...(completed.find(r => r.id === run.value)?.agents || []).map(a => {
      const option = el("option", a); option.value = a; return option;
    }));
  };
  run.onchange = updateAgents; updateAgents();
  const scope = rsiSelect([["failed", "运行异常尝试"], ["all", "该 Agent 的全部已记录尝试"]], "诊断范围");
  const objective = el("textarea"); objective.rows = 3; objective.maxLength = 16000;
  objective.value = "基于测评和介入记录定位 harness 问题，形成可验证的代码改进与回归测试。";
  const create = el("button", "冻结诊断材料", "button primary");
  create.disabled = !completed.length;
  create.onclick = async () => {
    create.disabled = true;
    try {
      const detail = await api("/api/runs/" + run.value);
      const trials = scope.value === "all" ? detail.trials.filter(t => t.agent === agent.value).map(t => t.trial) : null;
      await api("/api/rsi/cases", { method: "POST", body: JSON.stringify({ run_id: run.value, agent: agent.value, trials, objective: objective.value }) });
      await renderRsi(token);
    } catch (error) { notice(error.message); create.disabled = false; }
  };
  cases.append(rsiField("来源实验", run), rsiField("Agent", agent), rsiField("范围", scope), rsiField("改进目标", objective), create);
  view.append(cases);
  const caseArchive = el("section", undefined, "panel");
  caseArchive.append(el("h2", "诊断案例档案"), table(["案例", "Agent", "尝试", "材料状态", "采集时间"], data.cases.map(c => [
    link(c.id.slice(0,8), "#rsi-case/" + c.id), c.agent, c.trials.join("；"), c.status === "ready" ? "已冻结" : "采集中", time(c.created_at)
  ])));
  view.append(caseArchive);
  const candidate = el("section", undefined, "panel form-panel");
  candidate.append(el("h2", "2. 创建候选版本"));
  if (!data.profiles.length) candidate.append(el("p", "尚未配置执行 Agent。可先冻结证据；管理员在 rsi-profiles 中配置可插拔适配器与检查命令后，即可开始改进。", "callout"));
  const caseSelect = rsiSelect(data.cases.filter(c => c.status === "ready").map(c => [c.id, `${c.agent} · ${c.trials.length} 次尝试 · ${c.id.slice(0,8)}`]), "诊断案例");
  const profile = rsiSelect(data.profiles.map(p => [p.id, `${p.id} · ${p.version}`]), "执行配置");
  const parent = rsiSelect([["", "配置中固定的源码版本"], ...data.candidates.filter(c => c.phase === "validated").map(c => [c.id, `${c.id.slice(0,8)} · ${(c.commit || "").slice(0,8)}`])], "父候选");
  const start = el("button", "创建并运行改进", "button primary");
  start.disabled = !data.profiles.length || !data.cases.some(c => c.status === "ready");
  start.onclick = async () => {
    start.disabled = true;
    try {
      const result = await api("/api/rsi/candidates", { method:"POST", body:JSON.stringify({case_id:caseSelect.value, profile:profile.value, parent:parent.value || null}) });
      await api(`/api/rsi/candidates/${result.id}/actions`, {method:"POST",body:JSON.stringify({kind:"improve"})});
      location.hash = "#rsi/" + result.id;
    } catch(error) { notice(error.message); start.disabled = false; }
  };
  candidate.append(rsiField("案例", caseSelect), rsiField("执行配置", profile), rsiField("父版本", parent), start);
  view.append(candidate);
  const archive = el("section", undefined, "panel");
  archive.append(el("h2", "候选档案"), table(["候选", "父候选 / 源码", "检查状态", "最近复评", "PR"], data.candidates.map(c => [
    link(c.id.slice(0,8), "#rsi/"+c.id), c.parent_candidate?.slice(0,8) || c.parent_commit.slice(0,10), rsiPhases[c.phase] || c.phase,
    rsiAssessments[c.evaluations.at(-1)?.assessment] || "尚未复评", c.pr_url ? link("草稿 PR", c.pr_url) : "未发布"
  ])));
  view.append(archive);
}

async function renderRsiCandidate(token, identity, initial) {
  const [candidate, overview] = await Promise.all([api("/api/rsi/candidates/"+identity), api("/api/rsi")]);
  const source = await api("/api/rsi/cases/"+candidate.case);
  if (epoch !== token) return;
  const signature = JSON.stringify([candidate, overview.actions]);
  if (!initial && view.dataset.rsiSignature === signature) return;
  view.dataset.rsiSignature = signature;
  view.replaceChildren();
  const panel = el("section", undefined, "panel");
  panel.append(link("← RSI 档案", "#rsi", "button"), el("h1", "候选 " + identity.slice(0,8)),
    el("p", `${rsiPhases[candidate.phase] || candidate.phase} · 配置 ${candidate.profile_id}`),
    el("p", `父提交 ${candidate.parent_commit} → ${candidate.commit || "尚未生成提交"}`),
    el("p", source.objective));
  if(candidate.parent_candidate) panel.append(link("查看父候选", "#rsi/"+candidate.parent_candidate));
  panel.append(el("p",candidate.matches_measured_clean_commit === true ? "父提交与测评记录中的干净源码提交一致。" : "尚不能确认父提交等同于历史被测源码；本次复评会重新构建父版本与候选。","muted"));
  if(candidate.source_checkout_dirty_ignored) panel.append(el("p","原源码工作区存在未提交改动；本候选只使用所固定的 Git 提交。","callout"));
  if(candidate.diagnosis) panel.append(el("h2", "诊断与修改"), el("p", candidate.diagnosis.summary), el("p", candidate.diagnosis.changes),
    el("p", "证据引用："+candidate.diagnosis.evidence_refs.join(", ")), el("p", candidate.diagnosis.limitations || "未声明额外限制"));
  if(candidate.last_error) panel.append(el("p",candidate.last_error,"error"));
  const active = overview.actions.some(a => a.candidate === identity && ["queued","running"].includes(a.status));
  for (const [kind,label] of [["improve","运行改进"],["evaluate","运行父版本与候选复评"],["publish","发布草稿 PR"]]) {
    const button = el("button",label,"button");
    button.disabled = active || candidate.phase !== (kind === "improve" ? "created" : "validated");
    if(kind === "evaluate" && !candidate.can_evaluate) { button.disabled = true; button.title = "该候选配置尚未指定复评模板"; }
    if(kind === "publish" && !candidate.can_publish) { button.disabled = true; button.title = "该候选配置尚未指定 GitHub 仓库"; }
    button.onclick = async () => {
      button.disabled = true;
      try { await api(`/api/rsi/candidates/${identity}/actions`,{method:"POST",body:JSON.stringify({kind,confirm_unbounded:$("#rsi-live")?.checked || false})}); await renderRsiCandidate(token,identity,true); }
      catch(error) { notice(error.message); button.disabled = false; }
    };
    panel.append(button);
  }
  if(overview.allow_live) {
    const confirm = el("input"); confirm.type="checkbox"; confirm.id="rsi-live";
    panel.append(rsiField("若模板使用真实模型：确认本次父版本与候选两次复评不设费用上限",confirm));
  }
  if(candidate.pr_url) panel.append(link("打开草稿 PR",candidate.pr_url,"button"));
  panel.append(el("p","检查通过不等于效果改善。发布 PR 不会合并或部署。","muted"));
  view.append(panel);
  const validation = el("section",undefined,"panel");
  validation.append(el("h2","配置检查"),table(["检查", "退出码", "完成时间"],(candidate.checks || []).map(c=>[String(c.index+1),String(c.returncode),time(c.finished_at)])));
  validation.append(el("h2","验证记录"),table(["复评","结论","父分数","候选分数","运行异常（父 / 候选）","运行可靠性","限制"],candidate.evaluations.map(e=>[
    e.id.slice(0,8),rsiAssessments[e.assessment] || e.assessment,number(e.scores?.parent),number(e.scores?.candidate),
    e.errors ? `${e.errors.parent} / ${e.errors.candidate}` : "未知", ({improved:"异常减少",regressed:"异常增加",equal:"异常数相同"})[e.reliability_change] || "未比较", (e.issues || [e.error || ""]).join("；")
  ])),table(["操作","状态","时间","错误"],overview.actions.filter(a=>a.candidate===identity).map(a=>[a.kind,rsiPhases[a.status] || a.status,time(a.updated),a.error || ""])));
  view.append(validation);
  view.append(rsiEvidence(source));
  for(const [title,text] of [["候选补丁（最多展示 64 KiB）",candidate.patch],["PR 正文",candidate.pr_body]]) {
    if(!text) continue;
    const section=el("section",undefined,"panel"), pre=el("pre",text);
    section.append(el("h2",title),pre); view.append(section);
  }
}

function rsiEvidence(source) {
  const evidence = el("section",undefined,"panel");
  evidence.append(el("h2","诊断证据来源"),el("p","原始内容保存在本地证据包；本页只展示来源、截断和缺失。"),table(["引用","类别","原始路径","采集范围 / 错误"],source.evidence.map(e=>[
    e.id || "—",e.category,e.source || "—",e.error || `${e.offset}–${e.offset+e.bytes} / ${e.original_size} 字节${e.truncated ? "（截断）" : ""}`
  ])));
  return evidence;
}

async function renderRsiCase(token, identity) {
  const source = await api("/api/rsi/cases/"+identity);
  if(epoch !== token) return;
  view.replaceChildren();
  const panel = el("section",undefined,"panel");
  panel.append(link("← RSI 档案","#rsi","button"),el("h1","诊断案例 "+identity.slice(0,8)),
    el("p",source.objective),el("p",`${source.agent} · ${source.trials.join("；")} · ${source.exported_bytes} 字节已导出`),
    el("p","测评原生记录、守护介入记录和人工诊断快照按来源分别标注；缺失与截断不推断为事实。","callout"));
  view.append(panel,rsiEvidence(source));
}
