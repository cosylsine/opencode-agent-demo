// Real rebuilt container backend; TEST model, real OpenCode/MCP and UI.
const {writeFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {chromium, expect} = require(process.env.PLAYWRIGHT_MODULE || '../source/apps/dashboard/node_modules/@playwright/test');

(async () => {
  const origin = process.env.REHEARSAL_BROWSER_URL || 'http://127.0.0.1:3000';
  const output = resolve(__dirname, 'rebuild-20261018');
  const browser = await chromium.launch({headless: true, channel: 'chromium'});
  const reports = [];
  try {
    for (const missingAPIs of [false, true]) {
      const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
      if (missingAPIs) await context.addInitScript(() => {
        Array.prototype.at = undefined;
        Object.hasOwn = undefined;
        globalThis.structuredClone = undefined;
        Object.defineProperty(crypto, 'randomUUID', {value: undefined, configurable: true});
      });
      const page = await context.newPage();
      const errors = [], external = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        if (new URL(route.request().url()).origin !== origin) {
          external.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      await page.goto(origin);
      await expect(page.getByRole('textbox', {name: '发送消息给接待 Agent'})).toBeVisible();
      await page.getByRole('textbox', {name: '发送消息给接待 Agent'}).fill('你好');
      await page.getByRole('button', {name: '发送消息 ↗'}).click();
      await expect(page.locator('.chat-markdown').last()).toContainText('离线部署连通性测试', {timeout: 60000});
      await page.reload();
      await expect(page.locator('.chat-markdown').last()).toContainText('离线部署连通性测试');
      const navigate = name => page.getByRole('navigation', {name: '工作区', exact: true})
        .getByRole('button', {name, exact: true}).click();
      await navigate('Monitor 诊断');
      await page.locator('.run').first().click();
      await expect(page.locator('.chart').first()).toBeVisible();
      await navigate('B 回路仿真调优');
      await expect(page.locator('.tuning-chart')).toBeVisible();
      await expect(page.locator('.tuning-iteration')).toHaveCount(2);
      for (const name of ['Agent 中心', 'Skill 中心', 'Tool 中心', '接待对话']) {
        await navigate(name);
        await expect(page.locator('.workspace-title h1')).toContainText(name);
      }
      await page.getByRole('textbox', {name: '发送消息给接待 Agent'}).fill('多行草稿\n'.repeat(60));
      await expect(page.getByRole('button', {name: '发送消息 ↗'})).toBeInViewport();
      await page.screenshot({path: resolve(output, missingAPIs ? 'browser-compat.png' : 'browser.png')});
      expect(errors).toEqual([]);
      expect(external).toEqual([]);
      reports.push({missingAPIs, chat: 'passed', history: 'passed', monitorCurve: 'passed',
        tuningCurveAndTrials: 'passed', navigation: 'passed', longDraft: 'passed', errors, external});
      await context.close();
    }
    const result = {browser: browser.version(), url: origin,
      backend: 'real rebuilt image: backend/OpenCode/MCP/sampler; TEST model',
      limitation: 'Missing API simulation in modern Chromium, not actual Chrome 86/Windows 360', reports};
    writeFileSync(resolve(output, 'browser.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result, null, 2));
  } finally {await browser.close()}
})().catch(error => {console.error(error); process.exitCode = 1});
