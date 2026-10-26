"use strict";

function localHeader(title, description) {
  const head = el("section", undefined, "page-head"), text = el("div");
  text.append(el("div", "LOCAL WORKSPACE", "eyebrow"), el("h1", title), el("p", description));
  head.append(text); return head;
}
function localInput(label, value = "", type = "text") {
  const input = el("input"); input.type = type; input.value = value;
  input.setAttribute("aria-label", label); return input;
}
async function renderIntegrations(token) {
  const [catalog, jobs, history] = await Promise.all([api("/api/integrations"), api("/api/operations"), api("/api/runs")]);
  if (epoch !== token) return;
  view.replaceChildren(localHeader("接入自己的 Agent，准备对照版本。", "构建保留源码、配方与产物身份；运行检查和严格可比性分别记录。"));
  const builtins = rsiPanel("内置 Agent", "安装会下载固定源码及构建依赖；下载大小未知。首次安装需先在本机准备下面的固定 Docker 镜像。失败只影响该版本。");
  for (const item of catalog.builtins) {
    const box = el("div", undefined, "rsi-section-head");
    box.append(el("h3", item.id + " · " + ({runnable: "运行检查通过", unchecked: "待运行检查", unavailable: "未安装"}[item.availability])),
      el("p", item.reason || "可用于控制实验和描述性对比"), el("code", "docker pull " + item.build_image),
      el("p", `源码 ${item.source.commit} · 产物 ${item.artifact_origin}`), el("p", item.limitations.join("；")));
    box.append(rsiButton("安装固定版本", async () => {
      try { await api("/api/integrations/build", {method: "POST", body: JSON.stringify({builtin: item.id})}); await renderIntegrations(token); }
      catch (e) { notice(e.message); }
    })); builtins.append(box);
  }
  const own = rsiPanel("导入自己的 Agent", "提供本机干净源码目录与标准配方路径。配方定义构建、模型协议和 CLI / Python 入口；操作经本机 Manager 排队执行。");
  const source = localInput("源码目录"), recipe = localInput("配方路径");
  own.append(rsiField("源码目录", source), rsiField("配方路径", recipe), rsiButton("构建自己的版本", async () => {
    try { await api("/api/integrations/build", {method: "POST", body: JSON.stringify({source: source.value, recipe: recipe.value})}); await renderIntegrations(token); }
    catch (e) { notice(e.message); }
  }, true));
  const queue = rsiPanel("构建状态", "构建失败保留错误与证据；取消由 Manager 清理资源。"), rows = [];
  for (const job of jobs.operations.filter(j => j.kind === "integration")) {
    const action = el("div");
    if (["queued", "running", "held"].includes(job.status)) action.append(rsiButton("取消", async () => {
      try { await api(`/api/operations/${job.id}/cancel`, {method: "POST", body: "{}"}); await renderIntegrations(token); }
      catch (e) { notice(e.message); }
    }));
    rows.push([job.id.slice(0, 12), labels[job.status] || job.status, job.error || "—", action]);
  }
  queue.append(table(["作业", "状态", "原因", "操作"], rows), rsiButton("刷新状态", () => renderIntegrations(token)));
  const releases = rsiPanel("已构建版本与接入检查", "先创建一个模拟模型控制实验，再选择它核验该版本。检查不新增模型调用，也不会自行授予完整可比性。");
  const release = rsiSelect(catalog.releases.map(r => [r.release_id, `${r.release.agent_id} · ${r.release_id.slice(0, 12)}`]), "检查版本");
  const runs = rsiSelect(history.runs.filter(r => ["completed", "failed", "interrupted", "cancelled"].includes(r.status)).map(r => [r.id, r.name + " · " + modes[r.mode]]), "检查实验");
  const report = el("pre");
  releases.append(rsiField("版本", release), rsiField("已结束实验", runs), rsiButton("核验运行证据", async () => {
    try { report.textContent = JSON.stringify(await api("/api/integrations/check", {method: "POST", body: JSON.stringify({release_id: release.value, run_id: runs.value})}), null, 2); }
    catch (e) { notice(e.message); }
  }), report, link("创建控制实验或对比 →", "#setup", "button primary"));
  view.append(builtins, own, queue, releases);
}

async function renderLocalSetup(token) {
  const [catalog, templates, pool] = await Promise.all([api("/api/integrations"), api("/api/templates"), api("/api/models")]);
  if (epoch !== token) return;
  view.replaceChildren(localHeader("用同一模型，比较不同 Harness。", "选择版本与题目，预检冻结条件后启动。先用模拟模型完成接入检查，再进行真实模型实验。"));
  const panel = rsiPanel("实验条件", "所有被测 Agent 共享本次模型配置。真实执行需要服务开启 --allow-live，并在预检后确认费用。"), form = el("form"), selected = [];
  for (const item of catalog.releases) {
    const box = localInput("选择 " + item.release.agent_id, "", "checkbox");
    selected.push([box, item.release_id]); form.append(rsiField(`${item.release.agent_id} · ${item.release_id.slice(0, 12)}`, box));
  }
  if (!selected.length) form.append(link("先接入或安装 Agent →", "#integrations", "button"));
  const template = rsiSelect(templates.templates.map(t => [t.id, `${t.benchmark} · ${t.tasks.length} 题 · ${t.id}`]), "Benchmark 模板");
  const mode = rsiSelect([["mock", "模拟模型 · 接入检查"], ["live", "真实模型 · 能力对比"]], "模型模式");
  const model = rsiSelect([["", "使用实验模型绑定"], ...pool.models.map(m => [m.id, m.label + " · " + m.name])], "统一模型");
  const count = localInput("题目数", "2", "number"), seed = localInput("抽样种子", "0", "number"); count.min = "1"; count.max = "500";
  const attempts = localInput("每题次数", "1", "number"); attempts.min = "1"; attempts.max = "100";
  form.append(rsiField("Benchmark / 任务模板", template), rsiField("模型模式", mode), rsiField("统一模型", model), link("配置 API URL、模型 ID 和密钥 →", "#models"), rsiField("题目数", count), rsiField("抽样种子", seed), rsiField("每题次数", attempts));
  const review = rsiPanel("执行预检", "预检不调用模型。条件变化后必须重新预检。"), details = el("div");
  const paid = localInput("确认真实模型费用", "", "checkbox"), launch = rsiButton("按这些条件运行", async () => {
    try { launch.disabled = true; const run = await api("/api/runs", {method: "POST", body: JSON.stringify({...frozen.request, preflight_digest: frozen.report.digest, confirm_unbounded: paid.checked})}); location.hash = "#run/" + run.id; }
    catch (e) { notice(e.message); launch.disabled = false; }
  }, true); launch.disabled = true;
  let frozen = null, revision = 0;
  form.oninput = () => { ++revision; frozen = null; launch.disabled = true; details.replaceChildren(el("p", "条件已变更，请重新预检。")); };
  const preview = rsiButton("预检实验", async () => {
    const requestedRevision = ++revision;
    frozen = null; launch.disabled = true;
    const request = {template: template.value, releases: selected.filter(([box]) => box.checked).map(([, id]) => id), model_mode: mode.value, sample_count: Number(count.value), sample_seed: Number(seed.value), attempts: Number(attempts.value)};
    if (mode.value === "live" && model.value) request.model_id = model.value;
    try {
      const report = await api("/api/runs/preflight", {method: "POST", body: JSON.stringify(request)});
      if (epoch !== token || revision !== requestedRevision) return;
      frozen = {request, report};
      details.replaceChildren(el("p", `${report.planned_attempts} 次尝试 · 可用磁盘 ${rsiBytes(report.free_disk_bytes)} · 预计费用：未知${report.paid ? "（涉及付费调用）" : "（模拟模型）"}`), el("p", report.downloads), ...report.warnings.map(w => el("p", w)));
      paid.checked = false; launch.disabled = report.paid;
      paid.onchange = () => { launch.disabled = !frozen || (report.paid && !paid.checked); };
    } catch (e) { notice(e.message); frozen = null; launch.disabled = true; }
  });
  form.onsubmit = e => e.preventDefault(); form.append(preview); panel.append(form);
  review.append(details, rsiField("确认真实模型费用不设美元硬上限", paid), launch, link("查看已有逐题对比 →", "#compare", "button"));
  view.append(panel, review);
}
