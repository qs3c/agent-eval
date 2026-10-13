// Run with Node's test runner and jsdom on NODE_PATH (see docs/rsi.md).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const staticDir = path.join(__dirname, '../src/agent_harness_eval/static');
const app = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8').split('window.addEventListener("hashchange", route);')[0];
const rsi = fs.readFileSync(path.join(staticDir, 'rsi.js'), 'utf8');
const source = { id: 'a'.repeat(32), agent: 'bkcrab', trials: ['django__django-15022__attempt'], objective: 'Fix process cleanup', status: 'ready', created_at: '2026-10-12T08:20:00Z', exported_bytes: 4096, evidence: [] };
const profile = { id: 'bkcrab', version: 'test', evaluation: true, publication: true };
const candidate = { id: 'b'.repeat(32), case: source.id, profile_id: 'bkcrab', parent_commit: 'c'.repeat(40), phase: 'created', evaluations: [], can_evaluate: true, can_publish: true, created_at: source.created_at };
function setup(t, handler, full = false) {
  const dom = new JSDOM(full ? fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8') : '<div id="notice"></div><div id="view"></div>', { url: 'http://localhost/#rsi', runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  dom.window.fetch = async (url, options = {}) => {
    const result = await handler(url, options);
    return { ok: !result?.detail, json: async () => result };
  };
  dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new dom.window.Event('close')); };
  const context = dom.getInternalVMContext();
  vm.runInContext(app + '\n' + rsi + '\n' + fs.readFileSync(path.join(staticDir, 'models.js'), 'utf8') + '\n' + fs.readFileSync(path.join(staticDir, 'comparison.js'), 'utf8'), context);
  return { window: dom.window, doc: dom.window.document, run: code => vm.runInContext(code, context) };
}
const overview = (extra = {}) => ({ cases: [source], candidates: [], actions: [], profiles: [], allow_live: false, ...extra });
const clickNamed = (doc, label) => {
  const button = [...doc.querySelectorAll('button')].find(b => b.textContent === label);
  assert.ok(button, label); button.click(); return button;
};
const tick = () => new Promise(resolve => setImmediate(resolve));
const poolModel = {id:'d'.repeat(32), label:'编码模型', name:'deepseek-flash', api_base:'https://api.deepseek.com', messages_base:'https://api.deepseek.com/anthropic', has_api_key:true, max_tokens:32768, context_window:1000000};
const executorSettings = { options: [{id:'bkcrab', name:'BKCrab', installed:true, description:'BKCrab 原生编码 Agent'}, {id:'dsh', name:'DSH', installed:true, description:'DeepSeek Harness · Standard'}], configured:false, executor:'bkcrab', model:'deepseek-flash', timeout_seconds:1800, has_api_key:true, ready:false, model_id:poolModel.id, models:[poolModel], bindings:{executor_bkcrab:poolModel.id,executor_dsh:poolModel.id} };

test('executor can be selected and saved in the page without editing profile files', async t => {
  let settings = {...executorSettings}, saved;
  const {run, doc, window} = setup(t, async (url, options) => {
    if (options.method === 'PUT') { saved = JSON.parse(options.body); settings = {...settings, ...saved, configured:true, ready:true}; return settings; }
    return url === '/api/rsi' ? overview({executor_settings:settings}) : {runs:[]};
  });
  await run('renderRsi(0)');
  assert.deepEqual([...doc.querySelector('[aria-label="执行 Agent"]').options].map(o => o.text), ['BKCrab', 'DSH']);
  const select = doc.querySelector('[aria-label="执行 Agent"]'); select.value = 'dsh'; select.dispatchEvent(new window.Event('change'));
  await doc.querySelector('.rsi-executor-form').onsubmit({preventDefault(){}});
  assert.deepEqual(saved, {executor:'dsh', model_id:poolModel.id, timeout_seconds:1800});
  assert.equal(doc.querySelector('[aria-label="执行 Agent"]').value, 'dsh');
  assert.match(doc.querySelector('.rsi-executor-status').textContent, /已启用/);
  assert.doesNotMatch(doc.body.textContent, /另存为|rsi-profiles/);
});

test('missing model links to global pool without credential fields or submitting', async t => {
  const {run, doc} = setup(t, async () => { throw Error('Must not submit'); });
  run(`view.append(rsiConfigNote({executor_settings:${JSON.stringify({...executorSettings, has_api_key:false, models:[], bindings:{}})}}))`);
  await doc.querySelector('form').onsubmit({preventDefault(){}});
  assert.equal(doc.querySelector('input[type=password]'), null);
  assert.equal(doc.querySelector('details'), null);
  assert.equal(doc.querySelector('a').getAttribute('href'), '#models');
  assert.equal(doc.querySelector('button[type=submit]').disabled, true);
});

test('candidate form uses the saved executor while preserving the target Agent', t => {
  const {run, doc} = setup(t, async () => ({}));
  run(`rsiCandidateDialog(0, ${JSON.stringify(overview({executor_settings:{...executorSettings, executor:'dsh'}, profiles:[{...profile,id:'builtin-bkcrab',name:'BKCrab',executor:'bkcrab'}, {...profile,id:'builtin-dsh',name:'DSH',executor:'dsh'}]}))})`);
  assert.equal(doc.querySelector('[aria-label="执行 Agent"]').value, 'builtin-dsh');
  assert.match(doc.querySelector('.rsi-inline-note').textContent, /被改进的 Agent：bkcrab/);
});

test('archive shows only task titles before diagnosis and ignores historical objectives', async t => {
  const { run, doc, window } = setup(t, async url => url === '/api/rsi' ? overview() : { runs: [] });
  await run('renderRsi(0)');
  assert.equal(doc.querySelector('article').textContent, 'django-15022');
  assert.equal(doc.querySelectorAll('select').length, 0);
  const search = doc.querySelector('input[type=search]');
  search.value = 'not found'; search.dispatchEvent(new window.Event('input'));
  assert.match(doc.body.textContent, /没有匹配的案例/);
  search.value = 'cleanup'; search.dispatchEvent(new window.Event('input'));
  assert.equal(doc.querySelectorAll('article').length, 0);
  search.value = 'django-15022'; search.dispatchEvent(new window.Event('input'));
  assert.equal(doc.querySelectorAll('article').length, 1);
  clickNamed(doc, '＋ 新建诊断案例');
  assert.match(doc.querySelector('dialog').textContent, /暂无可用的已结束实验/);
  assert.equal(doc.querySelector('dialog').getAttribute('aria-labelledby'), 'rsi-dialog-title');
});

test('evidence filters distinguish missing, truncated and complete entries with bounded paging', t => {
  const { run, doc, window } = setup(t, async () => ({}));
  const evidence = Array.from({ length: 45 }, (_, i) => ({ id: 'e' + i, category: i < 5 ? 'manual_diagnostic_snapshot' : 'agent_trace', source: `path/${i}.log`, offset: 0, bytes: 10, original_size: 10,
    ...(i === 0 ? { error: 'missing' } : i === 1 ? { truncated: true } : {}) }));
  run(`view.append(rsiEvidence(${JSON.stringify({ ...source, evidence })}))`);
  assert.equal(doc.querySelectorAll('tbody tr').length, 20);
  clickNamed(doc, '下一页'); assert.match(doc.querySelector('.rsi-pager').textContent, /第 2 \/ 3 页/);
  const status = doc.querySelector('[aria-label="材料状态"]'); status.value = 'missing'; status.dispatchEvent(new window.Event('change'));
  assert.equal(doc.querySelectorAll('tbody tr').length, 1);
  assert.match(doc.querySelector('tbody').textContent, /missing/);
  status.value = 'truncated'; status.dispatchEvent(new window.Event('change'));
  assert.equal(doc.querySelectorAll('tbody tr').length, 1);
  status.value = 'complete'; status.dispatchEvent(new window.Event('change'));
  assert.match(doc.querySelector('.rsi-pager').textContent, /43 项/);
  const category = doc.querySelector('[aria-label="证据来源"]'); category.value = 'manual_diagnostic_snapshot'; category.dispatchEvent(new window.Event('change'));
  assert.equal(doc.querySelectorAll('tbody tr').length, 3);
});

test('all-trials form refuses an empty selection instead of silently falling back to failed trials', async t => {
  const requests = [];
  const { run, doc } = setup(t, async (url, options) => { requests.push([url, options]); return { trials: [] }; });
  run(`rsiCaseDialog(0, {runs: [{id: 'run', name: 'Finished run', status: 'completed', agents: ['bkcrab']}]})`);
  assert.equal(doc.querySelector('textarea'), null);
  doc.querySelector('[aria-label="诊断范围"]').value = 'all';
  await doc.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(requests.length, 1);
  assert.match(doc.querySelector('.error').textContent, /暂无已记录尝试/);
  assert.equal(doc.querySelector('[type=submit]').disabled, false);
});

test('case form submits selected evidence without asking for an objective', async t => {
  let payload;
  const { run, doc, window } = setup(t, async (url, options) => {
    if (options.method === 'POST') { payload = JSON.parse(options.body); return source; }
    return { trials: [{ agent: 'bkcrab', trial: 'one' }, { agent: 'other', trial: 'two' }] };
  });
  run(`rsiCaseDialog(0, {runs: [{id: 'run', status: 'completed', agents: ['bkcrab']}]})`);
  assert.equal(doc.querySelector('textarea'), null);
  assert.doesNotMatch(doc.querySelector('dialog').textContent, /改进目标/);
  assert.equal(doc.querySelector('[type=submit]').textContent, '保存诊断材料');
  doc.querySelector('[aria-label="诊断范围"]').value = 'all';
  await doc.querySelector('form').onsubmit({ preventDefault() {} });
  assert.deepEqual(payload, { run_id: 'run', agent: 'bkcrab', trials: ['one'] });
  assert.equal(window.location.hash, '#rsi-case/' + source.id);
});

test('enqueue failure after candidate creation opens the created record without offering duplicate creation', async t => {
  const requests = [];
  const { run, doc, window } = setup(t, async (url, options) => {
    requests.push(url);
    return url.endsWith('/actions') ? { detail: 'Queue unavailable' } : candidate;
  });
  run(`rsiCandidateDialog(0, ${JSON.stringify(overview({ profiles: [profile] }))})`);
  await doc.querySelector('form').onsubmit({ preventDefault() {} });
  await tick();
  assert.equal(requests.filter(p => p === '/api/rsi/candidates').length, 1);
  assert.equal(window.location.hash, '#rsi/' + candidate.id);
  assert.equal(doc.querySelector('dialog'), null);
  assert.match(doc.querySelector('#notice').textContent, /候选已创建/);
});

test('validated candidate preserves zero scores and shows distinct verification states', async t => {
  const validated = { ...candidate, phase: 'validated', can_evaluate: false, evaluations: [{ id: 'e'.repeat(32), assessment: 'regressed', scores: { parent: 1, candidate: 0 } }] };
  const { run, doc } = setup(t, async url => url === '/api/rsi' ? overview() : url.includes('/cases/') ? source : validated);
  await run(`renderRsiCandidate(0, '${candidate.id}', true)`);
  assert.match(doc.querySelector('.rsi-candidate-status').textContent, /检查通过.*本组评测下降.*未发布/);
  assert.match(doc.body.textContent, /执行配置中未指定复评模板/);
  assert.ok([...doc.querySelectorAll('td')].some(td => td.textContent === '0'));
  assert.equal([...doc.querySelectorAll('button')].find(b => b.textContent === '运行父子复评').disabled, true);
});

test('recovered native evaluation shows original tasks and submits explicit spending confirmation', async t => {
  const restored = { ...candidate, phase: 'validated', evaluation_from_case: true, evaluation_tasks: ['django-15022'], evaluation_attempts: 2, evaluation_requires_live: true };
  let payload;
  const { run, doc } = setup(t, async (url, options) => {
    if (options.method === 'POST') { payload = JSON.parse(options.body); return {id: 'action'}; }
    return url === '/api/rsi' ? overview({allow_live: true}) : url.includes('/cases/') ? source : restored;
  });
  await run(`renderRsiCandidate(0, '${candidate.id}', true)`);
  assert.match(doc.body.textContent, /原任务重跑：django-15022/);
  assert.doesNotMatch(doc.body.textContent, /未指定复评模板/);
  const button = [...doc.querySelectorAll('button')].find(b => b.textContent === '运行父子复评');
  assert.equal(button.disabled, false);
  doc.querySelector('#rsi-live').checked = true;
  await button.onclick();
  assert.deepEqual(payload, {kind: 'evaluate', confirm_unbounded: true});
});

test('live-disabled server gives an actionable reason for recovered native evaluation', async t => {
  const restored = {...candidate, phase: 'validated', evaluation_requires_live: true};
  const { run, doc } = setup(t, async url => url === '/api/rsi' ? overview() : url.includes('/cases/') ? source : restored);
  await run(`renderRsiCandidate(0, '${candidate.id}', true)`);
  assert.match(doc.body.textContent, /服务需启用 --allow-live/);
  assert.equal([...doc.querySelectorAll('button')].find(b => b.textContent === '运行父子复评').disabled, true);
});

test('DSH targeted regression displays plan and individual outcomes separately from replay scores', async t => {
  const targeted = {...candidate, phase: 'validated', evaluation_kind: 'targeted_regression', evaluations: [
    {id: 'a'.repeat(32), kind: 'targeted_regression', assessment: 'improved',
      plan: {objective: '复现恢复故障', rationale: '连续三轮失败', limitations: '仅限本故障', evidence_refs: ['e1']},
      tests: [{package: './internal/agent', name: 'TestRecovery', role: 'regression', parent: 'fail', candidate: 'pass'}]}
  ]};
  const {run, doc} = setup(t, async url => url === '/api/rsi' ? overview() : url.includes('/cases/') ? source : targeted);
  await run(`renderRsiCandidate(0, '${candidate.id}', true)`);
  assert.match(doc.body.textContent, /由 DSH 根据故障证据与补丁制定针对性测试方案/);
  assert.match(doc.body.textContent, /连续三轮失败/);
  assert.match(doc.body.textContent, /TestRecovery故障回归failpass/);
  assert.doesNotMatch(doc.body.textContent, /原任务重跑/);
});

for (const [state, label] of [['draft', '草稿 PR'], ['ready', '待审阅 PR'], ['closed', 'PR 已关闭'], ['merged', 'PR 已合并'], [null, 'PR 状态待同步']]) {
  test(`candidate shows synchronized PR state ${state} and offers publish retry`, async t => {
    const published = { ...candidate, phase: 'validated', pr_url: 'https://github.com/example/repo/pull/7', pr_state: state,
      can_publish: true, pr_sync_error: 'GitHub unavailable', pr_ready_reason: '最新父子复评未明确改善，保留草稿。' };
    let payload;
    const { run, doc } = setup(t, async (url, options) => {
      if (options.method === 'POST') { payload = JSON.parse(options.body); return {id: 'action'}; }
      return url === '/api/rsi' ? overview() : url.includes('/cases/') ? source : published;
    });
    await run(`renderRsiCandidate(0, '${candidate.id}', true)`);
    assert.ok(doc.querySelector('.rsi-candidate-status').textContent.includes(label));
    assert.match(doc.body.textContent, /PR 同步失败，测评结果已保留/);
    assert.ok([...doc.querySelectorAll('a')].some(a => a.textContent === '打开 PR ↗'));
    if (state === 'draft') assert.match(doc.body.textContent, /最新父子复评未明确改善/);
    await [...doc.querySelectorAll('button')].find(b => b.textContent === '同步 PR').onclick();
    assert.equal(payload.kind, 'publish');
  });
}

test('accepted action remains disabled if the subsequent status refresh fails', async t => {
  let posted = false;
  const { run, doc } = setup(t, async (url, options) => {
    if (options.method === 'POST') { posted = true; return { id: 'action' }; }
    if (posted) throw Error('Status unavailable');
    return url === '/api/rsi' ? overview() : url.includes('/cases/') ? source : candidate;
  });
  await run(`renderRsiCandidate(0, '${candidate.id}', true)`);
  const button = [...doc.querySelectorAll('button')].find(b => b.textContent === '运行改进');
  await button.onclick();
  assert.equal(posted, true);
  assert.equal(button.disabled, true);
  assert.match(doc.querySelector('[role=alert]').textContent, /操作已加入队列/);
});

const modelData = {models:[poolModel], bindings:{executor_bkcrab:poolModel.id,executor_dsh:poolModel.id,experiment:poolModel.id,guardian:poolModel.id}, scenes:{executor_bkcrab:'执行 Agent · BKCrab',executor_dsh:'执行 Agent · DSH',experiment:'实验 Agent',guardian:'守护 Agent'}};
test('global model edit omits blank key and saves separate scene defaults', async t => {
  const calls=[];
  const {run, doc} = setup(t, async (url, options) => {if(options.method) calls.push([url,JSON.parse(options.body)]); return modelData;});
  await run('renderModels(0)');
  assert.equal(doc.querySelectorAll('.model-bindings-grid select').length,4);
  clickNamed(doc,'编辑'); doc.querySelector('[name=label]').value='新名称';
  await doc.querySelector('dialog form').onsubmit({preventDefault(){}});
  assert.equal(calls[0][1].label,'新名称'); assert.equal('api_key' in calls[0][1],false);
  await doc.querySelector('.model-bindings-form').onsubmit({preventDefault(){}});
  assert.equal(calls[1][0],'/api/model-bindings'); assert.equal(calls[1][1].guardian,poolModel.id);
});
test('bound model removal requires reassignment', async t => {
  const {run,doc} = setup(t,async () => modelData);
  await run('renderModels(0)'); clickNamed(doc,'移除');
  assert.match(doc.querySelector('dialog').textContent,/先在场景默认模型中更换/);
  assert.equal(doc.querySelector('dialog button[type=submit]'),null);
});
test('model save failure retains input and displays error', async t => {
  const {run,doc} = setup(t,async (url,options) => options.method ? {detail:'保存失败'} : modelData);
  await run('renderModels(0)');clickNamed(doc,'编辑');doc.querySelector('[name=label]').value='保留名称';
  await doc.querySelector('dialog form').onsubmit({preventDefault(){}});
  assert.match(doc.querySelector('dialog .error').textContent,/保存失败/);
  assert.equal(doc.querySelector('[name=label]').value,'保留名称');
  assert.equal(doc.querySelector('dialog button[type=submit]').disabled,false);
});

test('experiment form submits pool selections and leaves offline contracts unchanged', async t => {
  let saved;
  const template = {id:'live',name:'live-test',mode:'live',model:'legacy',agents:['bkcrab'],tasks:['task'],benchmark:'test',available:true,guardian_mode:'assist',defaults:{attempts:1,concurrency:1,timeout_seconds:300,cpus:2,memory_mb:4096}};
  const {run,doc,window} = setup(t,async (url,options) => {
    if (url === '/api/models') return modelData;
    if (options.method === 'POST') {saved=JSON.parse(options.body);return {id:'created'};}
    return {templates:[template,{...template,id:'offline',mode:'offline',guardian_mode:'observe'}],allow_live:true};
  },true);
  await run('renderCreate(0)');
  assert.equal(doc.querySelector('[aria-label="实验模型"]').value,poolModel.id);
  await doc.querySelector('#create-form').onsubmit({preventDefault(){}});
  assert.equal(saved.model_id,poolModel.id); assert.equal(saved.guardian_model_id,poolModel.id);
  const templates=doc.querySelector('#template');templates.value='offline';templates.dispatchEvent(new window.Event('change'));
  assert.equal(doc.querySelector('[aria-label="实验模型"]').disabled,true);
  assert.equal(doc.querySelector('[aria-label="守护模型"]').disabled,true);
  await doc.querySelector('#create-form').onsubmit({preventDefault(){}});
  assert.equal('model_id' in saved,false);assert.equal('guardian_model_id' in saved,false);
});


test('archive displays and searches an Agent diagnosis topic after analysis', async t => {
  const diagnosed = {...source, diagnosis_topic:'shell 超时后子进程残留', diagnosis_candidate_id:candidate.id};
  const {run, doc, window} = setup(t, async url => url === '/api/rsi' ? overview({cases:[diagnosed]}) : {runs:[]});
  await run('renderRsi(0)');
  assert.equal(doc.querySelector('article').textContent, 'django-15022shell 超时后子进程残留');
  const search = doc.querySelector('input[type=search]');
  search.value = '子进程'; search.dispatchEvent(new window.Event('input'));
  assert.equal(doc.querySelectorAll('article').length, 1);
});

test('case details retain evidence metadata and link to the diagnosis producing the topic', async t => {
  const diagnosed = {...source, diagnosis_topic:'shell 超时后子进程残留', diagnosis_candidate_id:candidate.id};
  const {run,doc} = setup(t, async url => url === '/api/rsi' ? overview() : diagnosed);
  await run(`renderRsiCase(0, '${source.id}')`);
  assert.match(doc.body.textContent, /诊断主题.*shell 超时后子进程残留/);
  assert.match(doc.body.textContent, /4.0 KiB/);
  assert.doesNotMatch(doc.body.textContent, /Fix process cleanup/);
  assert.ok([...doc.querySelectorAll('a')].some(a => a.getAttribute('href') === '#rsi/' + candidate.id));
});
