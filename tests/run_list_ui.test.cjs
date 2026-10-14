// NODE_PATH=<jsdom node_modules> node --test tests/run_list_ui.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const staticDir = path.join(__dirname, '../src/agent_harness_eval/static');
const app = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8')
  .split('window.addEventListener("hashchange", route);')[0];
const experiment = {
  id: 'swe80', name: 'swe-live-80-assist', benchmark: 'swe-verified-random-80',
  directory: 'swe-live-80', mode: 'live', category: 'real', status: 'failed',
  agents: ['bkcrab', 'dsh-standard'], accepted_agents: [],
  reviews: {
    bkcrab: { status: 'unreviewed', reason: '未复核' },
    'dsh-standard': { status: 'unreviewed', reason: '未复核' },
  },
  agent_descriptions: { bkcrab: 'BKCrab', 'dsh-standard': 'DSH Standard' },
  expected: 160, completed: 160, created: '2026-10-13T15:37:27Z',
  execution_summary: { finished: true, errors: 35, unscored: 35 },
};
function setup(t, records = [experiment]) {
  const dom = new JSDOM(fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8'),
    { url: 'http://localhost/#runs', runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  dom.window.fetch = async () => ({ ok: true, json: async () => ({ runs: records }) });
  const context = dom.getInternalVMContext();
  vm.runInContext(app + '\n' + fs.readFileSync(path.join(staticDir, 'trace.js'), 'utf8'), context);
  return { doc: dom.window.document, run: code => vm.runInContext(code, context) };
}

test('default list shows unreviewed real experiments and retains the reviewed filter', async t => {
  const mock = { ...experiment, id: 'mock', name: 'mock-only', category: 'selftest', mode: 'mock' };
  const { doc, run } = setup(t, [experiment, mock]);
  await run('renderRuns(0)');
  assert.equal(doc.querySelector('#filters .active').dataset.filter, 'real');
  assert.equal(doc.querySelectorAll('#run-rows tr').length, 1);
  assert.match(doc.querySelector('#run-rows').textContent, /swe-live-80-assist.*待复核/);
  assert.doesNotMatch(doc.querySelector('#run-rows').textContent, /mock-only/);
  doc.querySelector('[data-filter="effective"]').click();
  assert.equal(doc.querySelectorAll('#run-rows tr').length, 0);
  doc.querySelector('[data-filter="all"]').click();
  assert.equal(doc.querySelectorAll('#run-rows tr').length, 2);
});

test('finished experiment with partial exceptions shows counts and belongs to ended stats', async t => {
  const { doc, run } = setup(t);
  await run('renderRuns(0)');
  assert.equal(doc.querySelector('#run-rows .badge').textContent, '已结束 · 部分异常');
  assert.match(doc.querySelector('#run-rows').textContent, /35 次运行异常 · 35 次未评分/);
  assert.match(doc.querySelector('#run-rows').textContent, /160 \/ 160/);
  const ended = [...doc.querySelectorAll('.stat')].find(n => n.textContent.includes('执行已结束'));
  assert.equal(ended.querySelector('strong').textContent, '1');
});

test('incomplete and running experiments keep their lifecycle labels', t => {
  const { run } = setup(t);
  for (const [status, completed, label] of [
    ['incomplete', 159, '结果不完整'], ['running', 160, '执行中'],
    ['failed', 0, '执行异常'], ['interrupted', 100, '已中断'],
  ]) {
    const record = { ...experiment, status, completed,
      execution_summary: { finished: false, errors: 35, unscored: 35 } };
    const node = run(`runState(${JSON.stringify(record)})`);
    assert.equal(node.querySelector('.badge').textContent, label);
    assert.equal(node.querySelector('.run-sub'), null);
  }
});

test('missing scores and all-error attempts are distinguished', t => {
  const { run } = setup(t);
  for (const [errors, unscored, label] of [
    [0, 1, '已结束 · 评分不完整'], [160, 160, '已结束 · 全部异常'], [0, 0, '已完成'],
  ]) {
    const record = { ...experiment, status: errors || unscored ? 'failed' : 'completed',
      execution_summary: { finished: true, errors, unscored } };
    assert.equal(run(`runState(${JSON.stringify(record)})`).querySelector('.badge').textContent, label);
  }
});
