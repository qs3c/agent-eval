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
  return {doc:dom.window.document, window:dom.window, requests, replies, run:s=>vm.runInContext(s,context)};
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
test('empty experiment list links to the dedicated guide', async t => {
  const {doc,run} = setup(t);
  await run('renderRuns(0)');
  assert.ok(doc.querySelector('#empty a[href="#welcome"]'));
  assert.ok(doc.querySelector('#empty a[href="#setup"]'));
});

function executorReply() {
  return {allow_live:true, model_id:'model-one', models:[{id:'model-one',label:'Shared model',name:'fixed',has_api_key:true,messages_base:'https://fixture.invalid/messages'}],
    options:['opencode','dsh','hermes'].map(id=>({id,name:id==='opencode'?'OpenCode':id==='dsh'?'DSH':'Hermes',ready:true,protocol:id==='dsh'?'anthropic-messages':'openai-chat'}))};
}
function input(window, doc, label, value) {
  const field=doc.querySelector(`[aria-label="${label}"]`); field.value=value;
  field.dispatchEvent(new window.Event(field.tagName==='SELECT'?'change':'input',{bubbles:true})); return field;
}
test('automatic onboarding sends the explicitly selected public executor and model', async t => {
  const {doc,run,window,replies,requests}=setup(t);
  replies['/api/integrations/executors']=executorReply();
  await run('renderIntegrations(0)');
  assert.deepEqual([...doc.querySelector('[aria-label="接入执行器"]').options].map(o=>o.value),['opencode','dsh','hermes']);
  input(window,doc,'Agent 源码链接','https://github.com/example/agent');
  input(window,doc,'接入执行器','hermes');
  const paid=doc.querySelector('[aria-label="确认本次接入模型费用"]'); paid.checked=true; paid.dispatchEvent(new window.Event('change'));
  assert.equal(button(doc,'开始一键接入').disabled,false);
  button(doc,'开始一键接入').click();
  await new Promise(resolve=>setImmediate(resolve));
  const request=requests.find(([url])=>url==='/api/integrations/onboard');
  assert.deepEqual(JSON.parse(request[1].body),{repository:'https://github.com/example/agent',ref:'main',executor:'hermes',model_id:'model-one',confirm_paid:true});
  assert.ok(!requests.some(([url])=>url==='/api/rsi/executor-settings'));
});
test('manual onboarding copies instructions without paid setup and submits for platform admission', async t => {
  const {doc,run,window,replies,requests}=setup(t);
  replies['/api/integrations/executors']={options:[],models:[],allow_live:false};
  replies['/api/integrations/instructions']={instructions:'Read docs/agent-integration.md; submit recipe for platform checks.'};
  let copied=''; Object.defineProperty(window.navigator,'clipboard',{value:{writeText:async text=>{copied=text;}}});
  await run('renderIntegrations(0)');
  input(window,doc,'Agent 源码链接','https://github.com/example/agent');
  input(window,doc,'接入方式','manual');
  assert.equal(button(doc,'开始一键接入').parentElement.hidden,true);
  button(doc,'生成接入说明').click(); await new Promise(resolve=>setImmediate(resolve));
  button(doc,'复制接入说明').click(); await new Promise(resolve=>setImmediate(resolve));
  assert.match(copied,/docs\/agent-integration.md/);
  assert.ok(!requests.some(([url,options])=>options.method==='POST' && ['/api/integrations/onboard','/api/sources'].includes(url)));
  input(window,doc,'接入配方绝对路径','/tmp/integration/recipe.yaml');
  button(doc,'提交配方并由平台验收').click(); await new Promise(resolve=>setImmediate(resolve));
  const request=requests.find(([url])=>url==='/api/integrations/import');
  assert.equal(JSON.parse(request[1].body).recipe,'/tmp/integration/recipe.yaml');
});
test('unavailable executor and missing fee confirmation prevent automatic submission', async t => {
  const {doc,run,window,replies}=setup(t);
  const config=executorReply(); config.options[0].ready=false; config.options[0].reason='Runtime missing';
  replies['/api/integrations/executors']=config;
  await run('renderIntegrations(0)'); input(window,doc,'Agent 源码链接','https://github.com/example/agent');
  assert.equal(button(doc,'开始一键接入').disabled,true);
  input(window,doc,'接入执行器','dsh');
  assert.equal(button(doc,'开始一键接入').disabled,true);
});
