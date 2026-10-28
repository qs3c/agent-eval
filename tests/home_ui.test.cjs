// NODE_PATH=.cache/home-ui/node_modules node --test tests/home_ui.test.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dir = path.join(__dirname, '../src/agent_harness_eval/static');
function setup(t, hash = '') {
  const dom = new JSDOM(fs.readFileSync(path.join(dir, 'index.html'), 'utf8'), {
    url: 'http://localhost/' + hash, runScripts: 'outside-only',
  });
  t.after(() => dom.window.close());
  dom.window.scrollTo = () => {};
  const requests = [], replies = {
    '/api/runs': {runs: []}, '/api/integrations': {releases: [], builtins: []},
    '/api/models': {models: []}, '/api/rsi': {cases: [], candidates: [], actions: []},
  };
  const failures = new Set();
  dom.window.fetch = async (url, options = {}) => {
    requests.push([url, options]);
    if (failures.has(url)) throw Error('fixture service unavailable');
    return {ok: true, json: async () => replies[url]};
  };
  const context = dom.getInternalVMContext();
  for (const file of ['rsi.js', 'home.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(dir, file), 'utf8').split('window.addEventListener("hashchange", route);')[0], context);
  }
  return {window: dom.window, doc: dom.window.document, requests, replies, failures, run: s => vm.runInContext(s, context)};
}
function record(id, overrides = {}) {
  return {id, name: id, benchmark: 'controls', agents: ['agent-a'], status: 'completed', mode: 'mock',
    created: '2026-10-28T10:00:00Z', expected: 2, completed: 2, ...overrides};
}

test('fresh workspace opens the separate guide without making changes or running models', async t => {
  const {run, doc, window, requests} = setup(t);
  await run('route()');
  assert.equal(window.location.hash, '#welcome');
  assert.ok(doc.querySelector('.welcome-page'));
  assert.equal(doc.querySelector('.welcome-steps').children.length, 3);
  assert.equal(doc.querySelectorAll('table, .stats').length, 0);
  assert.ok(doc.querySelector('[data-nav="welcome"]').classList.contains('active'));
  assert.equal(doc.querySelector('#breadcrumb').textContent, '使用引导');
  assert.ok(requests.every(([, options]) => !options.method || options.method === 'GET'));
});

test('skip is remembered but the guide remains available explicitly', async t => {
  const {run, doc, window} = setup(t);
  await run('route()');
  doc.querySelector('.welcome-top a').onclick();
  assert.equal(window.localStorage.getItem('agent-harness-eval.home.v1'), '1');
  window.history.replaceState(null, '', '/');
  await run('route()');
  assert.equal(window.location.hash, '#home');
  assert.ok(doc.querySelector('.home-page'));
  window.history.replaceState(null, '', '#welcome');
  await run('route()');
  assert.ok(doc.querySelector('.welcome-page'));
  assert.equal(doc.querySelectorAll('.is-ready').length, 0);
});

test('existing configuration or work enters home, even when local storage is unavailable', async t => {
  const {run, window, replies} = setup(t);
  Object.defineProperty(window, 'localStorage', {get: () => {throw Error('blocked');}});
  for (const [source, key, value] of [
    ['/api/runs', 'runs', record('existing')],
    ['/api/integrations', 'releases', {release_id: 'one'}],
    ['/api/models', 'models', {has_api_key: false}],
    ['/api/rsi', 'cases', {id: 'case', trials: ['task'], diagnosis_topic: null}],
  ]) {
    replies[source][key] = [value];
    window.history.replaceState(null, '', '/');
    await run('route()');
    assert.equal(window.location.hash, '#home');
    replies[source][key] = [];
  }
  run('preferHome()');
  window.history.replaceState(null, '', '/');
  await run('route()');
  assert.equal(window.location.hash, '#home');
});

test('preparation uses actual evidence; missing credentials do not block a mock evaluation', async t => {
  const {run, doc, replies} = setup(t, '#welcome');
  replies['/api/integrations'].releases = [{release_id: 'one'}];
  replies['/api/models'].models = [{has_api_key: false}];
  await run('route()');
  assert.equal(doc.querySelectorAll('.welcome-step.is-ready').length, 1);
  assert.equal(doc.querySelector('.welcome-intro .primary').hash, '#setup');
  assert.match(doc.querySelector('.welcome-steps').textContent, /模拟评测可跳过/);
  replies['/api/models'].models[0].has_api_key = true;
  await run('route()');
  assert.equal(doc.querySelectorAll('.welcome-step.is-ready').length, 2);
  assert.match(doc.querySelector('.welcome-steps').textContent, /连通性待验证/);
});

test('partial outage is unknown instead of zero or a false first visit', async t => {
  const {run, doc, window, failures} = setup(t);
  failures.add('/api/runs'); failures.add('/api/integrations');
  await run('route()');
  assert.equal(window.location.hash, '#home');
  assert.match(doc.querySelector('.home-page').textContent, /状态未知/);
  assert.match(doc.querySelector('.landing-warning').textContent, /实验记录、Agent 版本暂时无法读取/);
  assert.doesNotMatch(doc.querySelector('.home-page').textContent, /当前没有需要关注|还没有实验/);
  window.history.replaceState(null, '', '#welcome');
  await run('route()');
  assert.equal(doc.querySelectorAll('.is-ready').length, 0);
  assert.match(doc.querySelector('.welcome-steps').textContent, /状态未知/);
});

test('home prioritizes running work, orders recent records and preserves errors in finished runs', async t => {
  const {run, doc, replies} = setup(t, '#home');
  replies['/api/runs'].runs = [
    record('old-active', {status: 'running', created: '2026-10-27T00:00:00Z'}),
    record('new-partial', {execution_summary: {finished: true, errors: 1, unscored: 0}}),
    record('paused', {status: 'paused', created: '2026-10-26T00:00:00Z'}),
  ];
  await run('route()');
  assert.equal(doc.querySelector('.home-focus h2').textContent, 'old-active');
  assert.equal(doc.querySelector('.home-page > .landing-panel .home-run-name').textContent, 'new-partial');
  assert.match(doc.querySelector('.home-bottom').textContent, /new-partial/);
  assert.match(doc.querySelector('.home-bottom').textContent, /部分异常/);
  assert.doesNotMatch(doc.querySelector('.home-bottom').textContent, /paused/);
});

test('home escapes untrusted names and preserves original undiagnosed case titles', async t => {
  const {run, doc, replies} = setup(t, '#home');
  replies['/api/runs'].runs = [record('safe', {name: '<img src=x onerror=alert(1)>'})];
  replies['/api/rsi'].cases = [{id: 'case-one', trials: ['原始题目'], diagnosis_topic: null}];
  await run('route()');
  assert.equal(doc.querySelectorAll('.home-page img').length, 0);
  assert.match(doc.querySelector('.home-page').textContent, /<img/);
  assert.equal(doc.querySelector('.home-case span').textContent, '原始题目');
  assert.equal(doc.querySelector('.home-case p'), null);
  replies['/api/rsi'].cases[0].diagnosis_topic = 'Agent 根据原始证据生成的中文诊断';
  await run('route()');
  assert.equal(doc.querySelector('.home-case p').textContent, 'Agent 根据原始证据生成的中文诊断');
});

test('late welcome response does not overwrite a new page and guide return appears only in its workflow', async t => {
  const {run, doc, window} = setup(t, '#welcome');
  // A deferred shared snapshot lets every source resolve without timers.
  let unblock;
  const pending = new Promise(resolve => {unblock = resolve;});
  window.fetch = async () => {await pending; return {ok: true, json: async () => ({runs: [], releases: [], models: [], cases: [], candidates: []})};};
  const rendering = run('renderWelcome(0)');
  run('++epoch; view.replaceChildren(el("p", "new page"));');
  unblock(); await rendering;
  assert.equal(doc.querySelector('#view').textContent, 'new page');
  run('updateGuideReturn("welcome"); updateGuideReturn("models");');
  assert.equal(doc.querySelector('#guide-return').hidden, false);
  run('updateGuideReturn("home"); updateGuideReturn("models");');
  assert.equal(doc.querySelector('#guide-return').hidden, true);
});

test('explicit experiment links bypass automatic entry selection', async t => {
  const {run, doc, requests} = setup(t, '#run/known');
  run('renderDetail = async (id) => view.replaceChildren(el("h1", id));');
  await run('route()');
  assert.equal(doc.querySelector('#view h1').textContent, 'known');
  assert.equal(requests.length, 0);
});
