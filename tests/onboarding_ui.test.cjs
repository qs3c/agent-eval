const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dir = path.join(__dirname, '../src/agent_harness_eval/static');
function setup(t, releases = []) {
  const dom = new JSDOM(fs.readFileSync(path.join(dir, 'index.html'), 'utf8'), {url:'http://localhost/#setup', runScripts:'outside-only'});
  t.after(() => dom.window.close());
  const requests = [];
  const replies = {
    '/api/integrations': {releases, builtins:[]}, '/api/models': {models:[]},
    '/api/templates': {templates:[{id:'controls',benchmark:'file-smoke',tasks:['a','b']},{id:'swe-controls',benchmark:'swe',tasks:['one']}]},
    '/api/runs': {runs:[]}, '/api/sources': {sources:[]}, '/api/operations': {operations:[]}, '/api/rsi/cycles': {cycles:[]}
  };
  dom.window.fetch = async (url, options = {}) => {requests.push([url,options]); return {ok:true,json:async()=>replies[url] || {}};};
  const context = dom.getInternalVMContext();
  vm.runInContext(fs.readFileSync(path.join(dir,'app.js'),'utf8').split('window.addEventListener("hashchange", route);')[0] + '\n' + ['rsi.js','integrations.js','comparison.js'].map(p=>fs.readFileSync(path.join(dir,p),'utf8')).join('\n'),context);
  return {doc:dom.window.document, window:dom.window, requests, run:s=>vm.runInContext(s,context)};
}
const button = (doc,name) => [...doc.querySelectorAll('button')].find(b=>b.textContent===name);
test('empty setup cannot preflight template agents and offers offline mode', async t => {
  const {doc,run,requests} = setup(t);
  await run('renderLocalSetup(0)');
  assert.equal(button(doc,'预检实验').disabled,true);
  button(doc,'预检实验').click();
  assert.ok(!requests.some(([url])=>url.endsWith('/preflight')));
  assert.ok(doc.querySelector('[aria-label="模型模式"] option[value="offline"]'));
});
test('detail polling refreshes newly completed traces without resetting unchanged evidence', async t => {
  const {doc,run} = setup(t);
  doc.querySelector('#view').innerHTML='<section id="detail-traces"></section>';
  run('var traceCalls=0; async function tracePicker(r,container) {traceCalls++; container.append(el("p", String(r.trials.length)));}');
  run('refreshDetailTraces({id:"run",trials:[]})');
  run('refreshDetailTraces({id:"run",trials:[{id:"one"}]})');
  const section=doc.querySelector('#detail-traces');
  run('refreshDetailTraces({id:"run",trials:[{id:"one"}]})');
  assert.equal(run('traceCalls'),2);
  assert.equal(doc.querySelector('#detail-traces'),section);
  assert.match(section.textContent,/1/);
});
test('comparison diagnosis freezes exactly the selected attempt without requesting an objective', async t => {
  const {doc,run,requests} = setup(t);
  const action=run('diagnosisFromTrial("run-one", "own", "failed-task__one")');
  doc.querySelector('#view').append(action); action.click();
  await new Promise(resolve=>setImmediate(resolve));
  const request=requests.find(([url])=>url==='/api/rsi/cases');
  assert.deepEqual(JSON.parse(request[1].body),{run_id:'run-one',agent:'own',trials:['failed-task__one']});
  assert.equal(action.disabled,true);
});
test('selecting a smaller benchmark adjusts sample count before submission', async t => {
  const {doc,run,window} = setup(t,[{release_id:'a',release:{agent_id:'own'}}]);
  await run('renderLocalSetup(0)');
  const select=doc.querySelector('[aria-label="Benchmark 模板"]');
  select.value='swe-controls'; select.dispatchEvent(new window.Event('change',{bubbles:true}));
  assert.equal(doc.querySelector('[aria-label="题目数"]').value,'1');
  assert.equal(doc.querySelector('[aria-label="题目数"]').max,'1');
});
test('first integration exposes basic import before optional evolution', async t => {
  const {doc,run} = setup(t);
  await run('renderIntegrations(0)');
  const headings=[...doc.querySelectorAll('h2')].map(n=>n.textContent);
  assert.ok(headings.indexOf('导入自己的 Agent')<headings.indexOf('版本更新与合并后复评'));
  assert.equal(button(doc,'核验运行证据').disabled,true);
  assert.equal(button(doc,'启动有界闭环').disabled,true);
});
test('empty experiment list explains the first steps', async t => {
  const {doc,run} = setup(t);
  await run('renderRuns(0)');
  assert.ok(doc.querySelector('#empty a[href="#integrations"]'));
  assert.ok(doc.querySelector('#empty a[href="#setup"]'));
});
