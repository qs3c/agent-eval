// NODE_PATH=/tmp/agent-eval-regrade-ui-tests/node_modules node --test tests/terminal_bench_ui.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const dir = path.join(__dirname, '../src/agent_harness_eval/static');

test('Terminal-Bench per-task limits remain unset when submitted from the browser', async t => {
  const dom = new JSDOM(fs.readFileSync(path.join(dir, 'index.html'), 'utf8'),
    {url: 'http://localhost/#create', runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const templates = [{id:'terminal', name:'terminal-bench-2-0', benchmark:'terminal-bench',
    mode:'offline', model:'mock', guardian_mode:null, available:true,
    agents:['oracle'], tasks:['alpha'], defaults:{attempts:1, concurrency:1,
      timeout_seconds:null, cpus:null, memory_mb:null}}];
  let submitted;
  dom.window.fetch = async (url, options) => ({ok:true, json:async () => {
    if (url === '/api/templates') return {templates};
    if (url === '/api/models') return {models:[], bindings:{}};
    if (url === '/api/runs') {submitted = JSON.parse(options.body); return {id:'fixture'};}
    throw new Error('Unexpected URL: ' + url);
  }});
  const context = dom.getInternalVMContext();
  const app = fs.readFileSync(path.join(dir, 'app.js'), 'utf8')
    .split('window.addEventListener("hashchange", route);')[0];
  vm.runInContext(app + '\n' + fs.readFileSync(path.join(dir, 'rsi.js'), 'utf8')
    + '\n' + fs.readFileSync(path.join(dir, 'models.js'), 'utf8')
    + '\n' + fs.readFileSync(path.join(dir, 'comparison.js'), 'utf8'), context);
  await vm.runInContext('renderCreate(0)', context);
  const form = dom.window.document.querySelector('#create-form');
  for (const key of ['timeout_seconds', 'cpus', 'memory_mb']) {
    assert.equal(form.elements[key].value, '');
    assert.equal(form.elements[key].required, false);
    assert.match(form.elements[key].placeholder, /每道题/);
  }
  await form.onsubmit({preventDefault(){}});
  assert.equal(submitted.attempts, 1);
  assert.equal(submitted.concurrency, 1);
  for (const key of ['timeout_seconds', 'cpus', 'memory_mb']) assert.equal(key in submitted, false);
  // Explicit resource changes still reach the backend as numeric overrides.
  form.elements.cpus.value = '4';
  await form.onsubmit({preventDefault(){}});
  assert.equal(submitted.cpus, 4);
});


test('SWE creation submits the explicit official regrading switch', async t => {
  const dom = new JSDOM(fs.readFileSync(path.join(dir, 'index.html'), 'utf8'),
    {url:'http://localhost/#create', runScripts:'outside-only'});
  t.after(() => dom.window.close());
  const templates = [{id:'swe', name:'swe-fixture', benchmark:'swe', official_regrade:true,
    mode:'offline', model:'mock', guardian_mode:null, available:true, agents:['oracle'], tasks:['alpha'],
    defaults:{attempts:1, concurrency:1, timeout_seconds:300, cpus:2, memory_mb:4096}}];
  let submitted;
  dom.window.fetch = async (url, options) => ({ok:true, json:async () => {
    if (url === '/api/templates') return {templates};
    if (url === '/api/models') return {models:[], bindings:{}};
    if (url === '/api/runs') {submitted=JSON.parse(options.body);return {id:'fixture'};}
    throw new Error(url);
  }});
  const context=dom.getInternalVMContext();
  const app=fs.readFileSync(path.join(dir,'app.js'),'utf8').split('window.addEventListener("hashchange", route);')[0];
  vm.runInContext(app+'\n'+['rsi.js','models.js','comparison.js'].map(f=>fs.readFileSync(path.join(dir,f),'utf8')).join('\n'),context);
  await vm.runInContext('renderCreate(0)',context);
  const form=dom.window.document.querySelector('#create-form');
  assert.equal(dom.window.document.querySelector('#official-regrade-option').hidden,false);
  assert.equal(form.elements.official_regrade.checked,true);
  form.elements.official_regrade.checked=false;
  await form.onsubmit({preventDefault(){}});
  assert.equal(submitted.official_regrade,false);
});
