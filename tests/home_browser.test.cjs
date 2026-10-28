// NODE_PATH=.cache/home-ui/node_modules AHE_BROWSER_CHANNEL=chrome node --test tests/home_browser.test.cjs
// API fixtures verify presentation and navigation; no real evaluation is submitted.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const staticDir = path.resolve(__dirname, '../src/agent_harness_eval/static');
const outputDir = path.resolve(__dirname, '../.cache/home-ui/screenshots');

test('welcome and home work in a browser at desktop and mobile widths', async t => {
  fs.mkdirSync(outputDir, {recursive: true});
  const replies = {
    '/api/runs': {runs: []}, '/api/integrations': {builtins: [], releases: []},
    '/api/models': {models: [], bindings: {}, scenes: {}},
    '/api/rsi': {cases: [], candidates: [], actions: []},
  };
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    if (replies[pathname]) {
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(replies[pathname])); return;
    }
    const name = pathname === '/' ? 'index.html' : pathname.replace(/^\/static\//, '');
    const file = path.resolve(staticDir, name);
    if (!file.startsWith(staticDir + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404); res.end(); return;
    }
    res.setHeader('Content-Type', {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}[path.extname(file)] || 'text/plain');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'");
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({headless: true, ...(process.env.AHE_BROWSER_CHANNEL ? {channel: process.env.AHE_BROWSER_CHANNEL} : {})});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width: 1600, height: 1000}, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const base = `http://127.0.0.1:${server.address().port}/`;
  await page.goto(base);
  await page.locator('.welcome-page').waitFor();
  assert.equal(new URL(page.url()).hash, '#welcome');
  await page.screenshot({path: path.join(outputDir, 'welcome-desktop.png'), fullPage: true});
  await page.getByRole('button', {name: '查看准备步骤'}).click();
  assert.equal(await page.locator('#welcome-steps').evaluate(node => document.activeElement === node), true);
  await page.getByRole('link', {name: '配置模型 →', exact: true}).click();
  await page.locator('.models-page').waitFor();
  assert.equal(await page.locator('#guide-return').isVisible(), true);
  await page.getByRole('link', {name: '返回使用引导 →'}).click();
  await page.locator('.welcome-page').waitFor();
  assert.equal(await page.locator('.welcome-step.is-ready').count(), 0);
  await page.getByRole('link', {name: '先进入工作台 ↗'}).click();
  await page.locator('.home-page').waitFor();
  await page.goto(base);
  await page.locator('.home-page').waitFor();
  assert.equal(new URL(page.url()).hash, '#home');
  await page.screenshot({path: path.join(outputDir, 'home-empty-desktop.png'), fullPage: true});

  replies['/api/runs'].runs = [
    {id: 'fixture-running', name: 'agent-baseline-comparison', benchmark: 'terminal-bench', mode: 'live', agents: ['my-agent', 'OpenCode'], status: 'running', completed: 12, expected: 40, created: '2026-10-28T08:30:00Z'},
    {id: 'fixture-partial', name: 'controls-regression', benchmark: 'controls', mode: 'mock', agents: ['my-agent'], status: 'completed', completed: 4, expected: 4, created: '2026-10-28T07:00:00Z', execution_summary: {finished: true, errors: 1, unscored: 0}},
    {id: 'fixture-complete', name: 'first-agent-evaluation', benchmark: 'controls', mode: 'mock', agents: ['my-agent', 'Hermes'], status: 'completed', completed: 4, expected: 4, created: '2026-10-27T06:00:00Z'},
  ];
  replies['/api/integrations'].releases = [{release_id: 'fixture-a'}, {release_id: 'fixture-b'}];
  replies['/api/models'].models = [{has_api_key: true}];
  replies['/api/rsi'].cases = [{id: 'fixture-case', trials: ['terminal-file-search'], diagnosis_topic: '搜索结果缺少复核，导致错误路径被继续使用'}];
  await page.getByRole('button', {name: '刷新状态'}).click();
  await page.locator('.home-focus h2').filter({hasText: 'agent-baseline-comparison'}).waitFor();
  await page.screenshot({path: path.join(outputDir, 'home-populated-desktop.png'), fullPage: true});
  assert.match(await page.locator('.home-bottom').innerText(), /部分异常/);
  for (const width of [1280, 900, 390]) {
    await page.setViewportSize({width, height: 900});
    for (const destination of ['home', 'welcome']) {
      await page.goto(base + '#' + destination);
      await page.locator('.' + destination + '-page').waitFor();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${destination} must not overflow at ${width}px`);
      if (width === 390) await page.screenshot({path: path.join(outputDir, destination + '-mobile.png'), fullPage: true});
    }
  }
  // Narrow-screen navigation keeps all destinations reachable.
  await page.locator('.brand').click();
  await page.locator('.home-page').waitFor();
  assert.equal(await page.locator('#guide-return').isVisible(), false);
  assert.deepEqual(errors, []);
});
