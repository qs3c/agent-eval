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
function setup(t, handler) {
  const dom = new JSDOM('<div id="notice"></div><div id="view"></div>', { url: 'http://localhost/#rsi', runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  dom.window.fetch = async (url, options = {}) => {
    const result = await handler(url, options);
    return { ok: !result?.detail, json: async () => result };
  };
  dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new dom.window.Event('close')); };
  const context = dom.getInternalVMContext();
  vm.runInContext(app + '\n' + rsi, context);
  return { window: dom.window, doc: dom.window.document, run: code => vm.runInContext(code, context) };
}
const overview = (extra = {}) => ({ cases: [source], candidates: [], actions: [], profiles: [], allow_live: false, ...extra });
const clickNamed = (doc, label) => {
  const button = [...doc.querySelectorAll('button')].find(b => b.textContent === label);
  assert.ok(button, label); button.click(); return button;
};
const tick = () => new Promise(resolve => setImmediate(resolve));

test('archive exposes useful titles, searchable objectives and honest empty states', async t => {
  const { run, doc, window } = setup(t, async url => url === '/api/rsi' ? overview() : { runs: [] });
  await run('renderRsi(0)');
  assert.match(doc.querySelector('article').textContent, /django \/ django-15022/);
  assert.equal(doc.querySelectorAll('select').length, 0);
  const search = doc.querySelector('input[type=search]');
  search.value = 'not found'; search.dispatchEvent(new window.Event('input'));
  assert.match(doc.body.textContent, /没有匹配的案例/);
  search.value = 'cleanup'; search.dispatchEvent(new window.Event('input'));
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
  doc.querySelector('textarea').value = 'Fix the failure';
  doc.querySelector('[aria-label="诊断范围"]').value = 'all';
  await doc.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(requests.length, 1);
  assert.match(doc.querySelector('.error').textContent, /暂无已记录尝试/);
  assert.equal(doc.querySelector('[type=submit]').disabled, false);
});

test('case form keeps the selected source and objective in the request', async t => {
  let payload;
  const { run, doc, window } = setup(t, async (url, options) => {
    if (options.method === 'POST') { payload = JSON.parse(options.body); return source; }
    return { trials: [{ agent: 'bkcrab', trial: 'one' }, { agent: 'other', trial: 'two' }] };
  });
  run(`rsiCaseDialog(0, {runs: [{id: 'run', status: 'completed', agents: ['bkcrab']}]})`);
  doc.querySelector('textarea').value = '  Specific objective  ';
  doc.querySelector('[aria-label="诊断范围"]').value = 'all';
  await doc.querySelector('form').onsubmit({ preventDefault() {} });
  assert.deepEqual(payload, { run_id: 'run', agent: 'bkcrab', objective: 'Specific objective', trials: ['one'] });
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
