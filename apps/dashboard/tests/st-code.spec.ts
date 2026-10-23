import {expect, test} from '@playwright/test';
import {loginForBrowser} from './office-helpers';

test.beforeEach(async({page,request})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
});

test('ST 定制入口、完整任务、代码差异与下载',async({page})=>{
  await page.goto('/');
  const nav=page.locator('.of-sidebar nav');
  await expect(nav.getByRole('button',{name:'B 回路仿真调优',exact:true})).toBeVisible();
  await nav.getByRole('button',{name:'ST 代码优化',exact:true}).click();
  await expect(page.locator('.of-topbar h1')).toHaveText('ST 代码优化');
  await page.getByRole('button',{name:'载入示例'}).click();
  const title='ST 浏览器验收 '+Date.now();
  await page.getByRole('textbox',{name:'任务名称',exact:true}).fill(title);
  await expect(page.getByRole('button',{name:'开始优化',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'开始优化',exact:true}).click();
  await expect(page.getByRole('heading',{name:title,exact:true})).toBeVisible({timeout:15000});
  await expect(page.getByRole('status').filter({hasText:'已完成 · 待工程验证'})).toBeVisible({timeout:15000});
  await expect(page.getByText('注释版有效词元与原文一致。',{exact:false})).toBeVisible();
  await page.getByRole('tab',{name:'注释代码',exact:true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('测试注释：保持原逻辑');
  await page.getByRole('tab',{name:'问题 (2)',exact:true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('除数为零');
  await expect(page.getByRole('tabpanel')).toContainText('原文第 13–13 行');
  await page.getByRole('tab',{name:'测试用例',exact:true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('尚未执行');
  await expect(page.locator('.st-case')).toHaveCount(5);
  await page.getByRole('tab',{name:'代码差异',exact:true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('+Ratio := 0.0;');
  const download=page.getByRole('link',{name:'fixed.st ↓',exact:true});
  const response=await page.request.get((await download.getAttribute('href'))!);
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain('Denominator <> 0.0');
  const url=page.url();
  await page.reload();
  await expect(page.getByRole('status').filter({hasText:'已完成 · 待工程验证'})).toBeVisible();
  expect(page.url()).toBe(url);
});

for(const width of [390,320]){
  test(`源码上传、操作选择与手机布局 ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:844});
    await page.goto('/#st-code');
    await expect(page.locator('.of-topbar h1')).toHaveText('ST 代码优化');
    const code="PROGRAM Demo\nVAR x : REAL; END_VAR\nx := 1.0;\nEND_PROGRAM";
    await page.getByLabel('上传 ST 源码').setInputFiles({name:'sample.st',mimeType:'text/plain',buffer:Buffer.from(code)});
    await expect(page.getByRole('textbox',{name:'ST 源代码',exact:true})).toHaveValue(code);
    await expect(page.getByText('已读取 sample.st',{exact:false})).toBeVisible();
    for(const name of ['代码注释','测试用例','问题检查','修复建议'])await page.getByRole('checkbox',{name,exact:false}).uncheck();
    await expect(page.getByRole('button',{name:'开始优化',exact:true})).toBeDisabled();
    await page.getByRole('checkbox',{name:'代码注释',exact:false}).check();
    await expect(page.getByRole('button',{name:'开始优化',exact:true})).toBeEnabled();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    expect(await page.locator('.st-workspace').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
    await page.getByLabel('上传 ST 源码').setInputFiles({name:'bad.py',mimeType:'text/plain',buffer:Buffer.from('bad')});
    await expect(page.getByRole('alert')).toContainText('请选择 .st、.scl 或 .txt');
    await expect(page.getByRole('textbox',{name:'ST 源代码',exact:true})).toHaveValue(code);
  });
}

test('仅加注释不显示未执行的缺陷审查结果',async({page})=>{
  await page.goto('/#st-code');
  await page.getByRole('button',{name:'载入示例'}).click();
  const title='仅注释验收 '+Date.now();
  await page.getByRole('textbox',{name:'任务名称',exact:true}).fill(title);
  for(const name of ['测试用例','问题检查','修复建议'])await page.getByRole('checkbox',{name,exact:false}).uncheck();
  await page.getByRole('button',{name:'开始优化',exact:true}).click();
  await expect(page.getByRole('heading',{name:title,exact:true})).toBeVisible({timeout:15000});
  await expect(page.getByRole('tab',{name:'注释代码',exact:true})).toBeVisible();
  await expect(page.getByRole('tab',{name:/问题/})).toHaveCount(0);
  await expect(page.getByRole('tab',{name:'修复代码',exact:true})).toHaveCount(0);
});

test('显示当前处理阶段并区分需要 PLC 时钟的人工用例',async({page})=>{
  const id='a0a0a0a0-0000-4000-8000-000000000001';
  let completed=false;
  await page.route('**/api/v1/st-runs/'+id,async route=>{
    await route.fulfill({json:{schema_version:'1.0',run_id:id,status:completed?'completed':'running',
      created_at:'2026-10-20T12:00:00Z',updated_at:'2026-10-20T12:00:01Z',stage:completed?'validation':'tests',stage_sessions:{},
      request:{title:'阶段与时钟用例',filename:'timer.st',code:'PROGRAM P\nEND_PROGRAM',actions:['test'],dialect:'generic',instructions:''},
      source_sha256:'abc',model_id:'test',attempt_count:1,validation_feedback:[],artifacts:[],error:null,
      result:completed?{summary:'测试设计',annotated_code:null,fixed_code:null,test_code:'PROGRAM Tests\nEND_PROGRAM',bugs:[],assumptions:[],
        tests:[{id:'T001',title:'跨扫描排放定时',category:'scan_cycle',execution:'manual',adaptation_note:'目标 PLC 持续运行满20秒并记录每扫描Q',preconditions:'从复位状态开始',steps:['进入排放状态并持续调用','等待20秒后读取Q'],expected:'计时到达后Q变为TRUE',related_bug_ids:[]}]}:null,
    }});
  });
  await page.goto('/#st-code/'+id);
  await expect(page.getByText('当前阶段：设计测试。',{exact:false})).toBeVisible();
  completed=true;
  await expect(page.getByRole('tab',{name:'测试用例',exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'测试用例',exact:true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('人工 / 目标 PLC 时钟验证');
  await expect(page.getByRole('tabpanel')).toContainText('目标 PLC 持续运行满20秒');
});
