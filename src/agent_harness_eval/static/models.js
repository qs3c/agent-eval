"use strict";

function poolSelect(data, label, selected, empty = "请选择模型") {
  const input = rsiSelect([["", empty], ...data.models.map(m => [m.id, `${m.label} · ${m.name}${m.has_api_key ? "" : " · 缺少凭据"}`])], label);
  input.value = selected || "";
  return input;
}

async function renderModels(token) {
  const data = await api("/api/models");
  if (epoch !== token) return;
  const shell = el("div", undefined, "models-page");
  const header = rsiHeader("模型设置", "统一管理模型与凭据，为不同 Agent 场景分配合适的模型。", [rsiButton("＋ 添加模型", () => modelDialog(token, data), true)]);
  header.querySelector('.eyebrow').textContent = "MODELS / 全局模型池";
  const pool = rsiPanel("全局模型池", "模型信息配置一次，多处复用。密钥只保存在服务端，编辑时留空即可保留。");
  const count = el("span", `${data.models.length} 个模型`, "rsi-badge neutral"); pool.querySelector('.rsi-section-head').append(count);
  if (!data.models.length) pool.append(rsiEmpty("还没有模型", "添加模型后，在下方为执行、实验和守护场景分配默认模型。", rsiButton("添加第一个模型", () => modelDialog(token, data), true)));
  else {
    const cards = el("div", undefined, "model-cards");
    for (const item of data.models) {
      const card = el("article", undefined, "model-card"), head = el("div", undefined, "model-card-head");
      head.append(el("h3", item.label), rsiBadge(item.has_api_key ? "凭据已配置" : "待配置凭据", item.has_api_key ? "success" : "warning"));
      const assigned = Object.entries(data.bindings).filter(([, id]) => id === item.id).map(([scene]) => data.scenes[scene]);
      const actions = el("div", undefined, "rsi-actions");
      actions.append(rsiButton("编辑", () => modelDialog(token, data, item)), rsiButton("移除", () => modelRemoveDialog(token, data, item)));
      card.append(head, el("p", item.name, "model-name mono"), el("p", item.api_base, "model-endpoint"), el("p", `最大输出 ${number(item.max_tokens)} · 上下文 ${number(item.context_window)}`, "muted"), el("p", item.messages_base ? "Chat Completions + Messages · 可用于 OpenCode / DSH / Hermes" : "Chat Completions · DSH 需补充 Messages 接口", "muted"), el("p", assigned.length ? "用于：" + assigned.join("、") : "尚未分配使用场景", "model-assignments"), actions);
      cards.append(card);
    }
    pool.append(cards);
  }
  const scenes = rsiPanel("场景默认模型", "每个场景独立选择。新任务保存配置快照，修改默认模型不影响已有任务。");
  const form = el("form", undefined, "model-bindings-form"), grid = el("div", undefined, "model-bindings-grid"), inputs = {};
  const descriptions = {executor_opencode:"OpenCode 分析证据、修改候选代码", executor_hermes:"Hermes 分析证据、修改候选代码", executor_dsh:"DeepSeek Harness 执行改进", experiment:"真实评测中各参与 Agent 共用，便于公平比较", guardian:"仅在实验启用自主介入时调用；不会自动开启守护"};
  for (const [scene, name] of Object.entries(data.scenes)) {
    const input = poolSelect(data, name, data.bindings[scene], scene.startsWith("executor_") ? "未指定（需选择后才能执行）" : "未指定（使用已有模板配置）"); inputs[scene] = input;
    grid.append(rsiField(name, input, descriptions[scene]));
  }
  const error = el("p", undefined, "error"); error.setAttribute("role", "alert");
  const save = rsiButton("保存场景分配", null, true); save.type = "submit";
  form.append(grid, error, save); scenes.append(form);
  form.onsubmit = async event => {
    event.preventDefault(); save.disabled = true; error.textContent = "";
    try {
      await api("/api/model-bindings", {method:"PUT", body:JSON.stringify(Object.fromEntries(Object.entries(inputs).map(([scene, input]) => [scene, input.value || null])))});
      if (epoch === token) { await renderModels(token); notice("场景默认模型已保存，将用于新建任务。"); }
    } catch (err) { error.textContent = err.message; }
    finally { save.disabled = false; }
  };
  shell.append(header, pool, scenes); view.replaceChildren(shell);
}

function modelDialog(token, data, item) {
  const {dialog, form, error} = rsiDialog(item ? "编辑模型" : "添加模型", "配置 Chat Completions 接口；DSH 还需服务支持 Messages。保存不会发起模型调用。");
  form.classList.add("model-edit-form");
  const fields = {};
  const specs = [
    ["label", "显示名称", item?.label || "", "例如：日常编码 / 深度诊断"],
    ["name", "模型 ID", item?.name || "", "例如：deepseek-flash"],
    ["api_base", "Chat Completions 接口地址", item?.api_base || "https://api.deepseek.com", "服务根地址或以 /v1 结尾；宿主机服务需使用容器可达地址，localhost 指向任务容器自身"],
    ["messages_base", "Messages 接口地址（DSH）", item?.messages_base || "", "DeepSeek 官方接口自动填写；其他服务按其文档配置"],
    ["api_key", "API Key", "", item?.has_api_key ? "已配置；留空保持原密钥" : "密钥仅保存在服务端"],
    ["max_tokens", "最大输出 Token", item?.max_tokens || 32768, "每次模型响应的输出上限"],
    ["context_window", "上下文窗口 Token", item?.context_window || 1000000, "必须大于最大输出 Token"]
  ];
  const grid = el("div", undefined, "model-edit-grid");
  for (const [key, label, value, hint] of specs) {
    const input = el("input"); input.name = key; input.value = value; input.required = !["messages_base", "api_key"].includes(key) || (key === "api_key" && !item?.has_api_key);
    if (key === "api_key") { input.type = "password"; input.autocomplete = "new-password"; }
    else if (["max_tokens", "context_window"].includes(key)) { input.type = "number"; input.min = 1; input.max = key === "max_tokens" ? 1048576 : 10000000; }
    else { input.type = key.endsWith("base") ? "url" : "text"; input.maxLength = key === "label" ? 80 : key === "name" ? 120 : 500; }
    fields[key] = input; grid.append(rsiField(label, input, hint));
  }
  const policyEnabled = el("input"); policyEnabled.type = "checkbox"; policyEnabled.checked = Boolean(item?.policy);
  const policyParameters = el("textarea"); policyParameters.value = JSON.stringify(item?.policy?.parameters || {temperature:1,top_p:1});
  const policyRequests = el("input"); policyRequests.type = "number"; policyRequests.min = 1; policyRequests.max = 1000000; policyRequests.value = item?.policy?.budget?.max_requests || 1000;
  grid.append(rsiField("启用固定模型策略", policyEnabled, "固定该场景的模型和参数；子调用不能通过请求体切换角色。旧实验快照保持不变。"),
    rsiField("统一采样/推理参数 JSON", policyParameters, "只填写服务支持的 temperature、top_p、seed、reasoning_effort、thinking、output_config；不支持时请求明确失败。"),
    rsiField("每次尝试的模型请求上限", policyRequests, "平台与被测角色独立计数；这不是美元费用保证。"));
  const save = rsiButton(item ? "保存模型" : "添加到模型池", null, true); save.type = "submit";
  form.append(grid, error, save);
  form.onsubmit = async event => {
    event.preventDefault(); if (save.disabled) return;
    const payload = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, ["max_tokens", "context_window"].includes(key) ? Number(input.value) : input.value.trim()]));
    if (payload.context_window <= payload.max_tokens) { error.textContent = "上下文窗口必须大于最大输出 Token。"; return; }
    if (!payload.api_key) delete payload.api_key;
    if (!payload.messages_base) payload.messages_base = null;
    try {
      payload.policy = policyEnabled.checked ? {parameters:JSON.parse(policyParameters.value),
        protocols:payload.messages_base || payload.api_base.replace(/\/v1\/?$/, "").replace(/\/$/, "") === "https://api.deepseek.com" ? ["openai-chat","anthropic-messages"] : ["openai-chat"],
        budget:{max_requests:Number(policyRequests.value)}} : null;
    } catch { error.textContent = "统一参数必须是有效 JSON 对象。"; return; }
    save.disabled = true; error.textContent = "";
    try {
      await api("/api/models" + (item ? "/" + item.id : ""), {method:item ? "PUT" : "POST", body:JSON.stringify(payload)});
      fields.api_key.value = "";
      if (epoch === token) { dialog.close(); await renderModels(token); notice("模型已保存。可在场景默认模型中分配使用。"); }
    } catch (err) { error.textContent = err.message; }
    finally { save.disabled = false; }
  };
}

function modelRemoveDialog(token, data, item) {
  const {dialog, form, error} = rsiDialog("移除模型", `从模型池移除「${item.label}」。已有任务的配置快照保留。`);
  if (Object.values(data.bindings).includes(item.id)) { form.append(el("p", "此模型仍有场景引用。请先在场景默认模型中更换分配，再移除。")); return; }
  const remove = rsiButton("确认移除", null); remove.type = "submit"; form.append(error, remove);
  form.onsubmit = async event => {
    event.preventDefault(); remove.disabled = true;
    try { await api("/api/models/" + item.id, {method:"DELETE", body:"{}"}); if (epoch === token) {dialog.close(); await renderModels(token);} }
    catch (err) { error.textContent = err.message; remove.disabled = false; }
  };
}
