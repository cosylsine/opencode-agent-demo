import {test,expect,type Page} from '@playwright/test';

import {loginForBrowser,openWorkspace} from './office-helpers';
test.beforeEach(async({page,request})=>{
  const state=await loginForBrowser(request);
  await page.context().addCookies(state.cookies);
});

async function openNavigation(page:Page){
  if(!await page.locator('.app-sidebar').isVisible())await page.locator('.sidebar-toggle').click();
}
async function navigate(page:Page,name:string){
  if(await page.locator('.sidebar-backdrop').isVisible())await page.getByRole('button',{name:'关闭导航',exact:true}).click();
  await page.locator('.of-sidebar nav').getByRole('button',{name,exact:true}).click();
}
test('B 回路显示负荷事件、五条共享时间轴响应与手机布局',async({page})=>{
  await openWorkspace(page);
  await navigate(page,'B 回路仿真调优');
  await page.getByRole('button',{name:'开始仿真调优'}).click();
  await expect(page.getByText('负荷事件',{exact:true})).toBeVisible();
  await expect(page.getByText('60s · 阶跃至 550 kW')).toBeVisible();
  await expect(page.locator('.tuning-chart')).toBeVisible();
  await expect(page.getByText('全部工况安全').first()).toBeVisible();
  await expect(page.locator('.tuning-iteration')).toHaveCount(2,{timeout:20000});
  await expect(page.getByText('改善示例给水响应')).toBeVisible();
  await expect(page.getByText('Agent 曾在探索初期停止提案，已安排一次复核。复核不增加仿真轮次；后续候选仍须通过相同的安全和改善判定。',{exact:true})).toBeVisible();
  await expect(page.locator('.tuning-controls select').last().locator('option')).toHaveCount(2);
  await page.screenshot({path:'../../.impeccable/review/tuning-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(page.locator('.tuning-chart')).toBeVisible();
  await page.screenshot({path:'../../.impeccable/review/tuning-mobile.png',fullPage:true});
});

test('B 回路老化状态可配置、保存、复用且不会被下次配置覆盖',async({page})=>{
  await openWorkspace(page);
  await navigate(page,'B 回路仿真调优');
  const editor=page.locator('.tuning-state-editor');
  await expect(editor.getByRole('slider')).toHaveCount(5);
  await editor.getByRole('button',{name:'随机生成状态',exact:true}).click();
  const randomValues=await editor.getByRole('slider').evaluateAll(elements=>elements.map(el=>Number((el as HTMLInputElement).value)));
  expect(randomValues.every(value=>value>=0&&value<=70)).toBe(true);
  await editor.getByRole('button',{name:'中度老化',exact:true}).click();
  await expect(editor.getByRole('slider').first()).toHaveValue('50');
  const pump=editor.getByRole('slider',{name:'给水泵老化程度',exact:true});
  await pump.focus();
  await pump.press('ArrowRight');
  await expect(pump).toHaveValue('51');
  await editor.getByLabel('状态名称').fill('浏览器老化回路');
  await pump.press('ArrowLeft');
  await pump.press('ArrowRight');
  await expect(editor.getByLabel('状态名称')).toHaveValue('浏览器老化回路');
  const start=page.getByRole('button',{name:'开始仿真调优',exact:true});
  await expect(start).toBeEnabled({timeout:20000});
  const submitted=page.waitForRequest(request=>request.method()==='POST'&&request.url().endsWith('/api/v1/tuning-runs'));
  await start.click();
  const body=(await submitted).postDataJSON();
  expect(body.plant_state).toEqual({name:'浏览器老化回路',aging:{feedwater_pump:0.51,feedwater_valve:0.5,boiler:0.5,turbine:0.5,condenser:0.5}});
  const saved=page.locator('.tuning-saved-state');
  await expect(saved.getByRole('heading')).toContainText('浏览器老化回路');
  await expect(saved.getByText('51%',{exact:true})).toBeVisible();
  await editor.getByRole('button',{name:'标称回路',exact:true}).click();
  await expect(pump).toHaveValue('0');
  await expect(saved.getByText('51%',{exact:true})).toBeVisible();
  await page.reload();
  await navigate(page,'B 回路仿真调优');
  await expect(saved.getByRole('heading')).toContainText('浏览器老化回路');
  await saved.getByRole('button',{name:'复用此状态'}).click();
  await expect(pump).toHaveValue('51');
  await expect(editor.getByLabel('状态名称')).toHaveValue('浏览器老化回路');
  await page.locator('.page-scroll').evaluate(el=>{el.scrollTop=0});
  await page.screenshot({path:'../../.impeccable/review/tuning-aging-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(pump).toBeVisible();
  await expect(saved).toBeVisible();
  await page.screenshot({path:'../../.impeccable/review/tuning-aging-mobile.png',fullPage:true});
});

test('B 回路将第 2、7 轮拒绝提案按数字顺序插回轨迹并计入总轮数',async({page,request})=>{
  let source=(await (await request.get('/api/v1/tuning-runs')).json()).items.find((run:{trials:unknown[]})=>run.trials.length);
  if(!source){
    const token=(await (await request.get('/api/v1/csrf-token')).json()).token;
    const response=await request.post('/api/v1/tuning-runs',{data:{schema_version:'1.0'},
      headers:{Origin:'http://127.0.0.1:18001','X-CSRF-Token':token,'Idempotency-Key':crypto.randomUUID()}});
    expect(response.status()).toBe(202);
    const {run_id}=await response.json();
    await expect.poll(async()=>{
      source=await (await request.get('/api/v1/tuning-runs/'+run_id)).json();
      return source.trials.length;
    }).toBeGreaterThan(0);
  }
  const base=source.trials.find((trial:{iteration:number})=>trial.iteration===0);
  const run={...source,run_id:crypto.randomUUID(),status:'COMPLETED',max_iterations:12,
    trials:Array.from({length:13},(_,iteration)=>({...base,trial_id:crypto.randomUUID(),iteration,
      pid:{...base.pid,kp:0.2+iteration*0.1}})).filter(trial=>![2,7].includes(trial.iteration)).reverse(),
    rejections:[{iteration:7,reason:'候选参数名不完整',agent_output:{reason:'缺少 ki 参数'}},
      {iteration:2,reason:'参数边界或 delta 不一致',agent_output:{reason:'测试被拒绝提案'}}]};
  run.best_trial_id=run.trials.find(trial=>trial.iteration===1)!.trial_id;
  await page.route('**/api/v1/tuning-runs**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/v1/tuning-runs')await route.fulfill({json:{schema_version:'1.0',items:[run]}});
    else if(path===`/api/v1/tuning-runs/${run.run_id}`)await route.fulfill({json:run});
    else if(path.endsWith('/trace'))await route.fulfill({json:{schema_version:'1.0',points:[]}});
    else await route.fallback();
  });
  await openWorkspace(page);
  await navigate(page,'B 回路仿真调优');
  const entries=page.locator('.tuning-iteration');
  await expect(entries).toHaveCount(13);
  await expect.poll(()=>entries.evaluateAll(elements=>elements.map(el=>Number(el.getAttribute('data-iteration')))))
    .toEqual(Array.from({length:13},(_,index)=>index));
  await expect(entries.nth(7)).toContainText('第 7 轮 · 参数提案被拒绝');
  await expect(entries.nth(7)).toContainText('候选参数名不完整');
  await expect(entries.nth(8)).toContainText('第 8 轮');
  await expect(entries.last()).toContainText('第 12 轮');
  await expect(page.locator('.tuning-summary')).toContainText('第 12/12 轮');
  await page.setViewportSize({width:390,height:844});
  await expect(entries.nth(7)).toContainText('第 7 轮 · 参数提案被拒绝');
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('Monitor 诊断双栏独立滚动，检查操作固定在右栏顶部',async({page,request})=>{
  test.setTimeout(120000);
  // A fresh migration has no browser-test history. Seed the records this
  // scrolling test needs through the isolated Fake backend's public API.
  const token=(await (await request.get('/api/v1/csrf-token')).json()).token;
  const existing=(await (await request.get('/api/v1/runs')).json()).items.length;
  for(let i=existing;i<16;i++){
    const response=await request.post('/api/v1/triggers',{
      data:{schema_version:'1.0',scenario_id:'improvable'},
      headers:{Origin:'http://127.0.0.1:18001','X-CSRF-Token':token,'Idempotency-Key':crypto.randomUUID()}
    });
    expect(response.status()).toBe(202);
    const {run_id}=await response.json();
    await expect.poll(async()=>(await (await request.get('/api/v1/runs/'+run_id)).json()).status,
      {timeout:15000}).toBe('MANUAL_REVIEW_REQUIRED');
  }
  await openWorkspace(page);
  await navigate(page,'Monitor 诊断');
  await expect(page.locator('.run').first()).toBeVisible();
  await page.locator('.run').first().click();
  await expect(page.locator('.summary')).toBeVisible();
  const geometry=await page.evaluate(()=>{
    const rail=document.querySelector('.app-sidebar')!;
    const history=document.querySelector('.history')!;
    const main=document.querySelector('.console-main')!;
    const shell=document.querySelector('.app-shell')!;
    return {gap:main.getBoundingClientRect().left-rail.getBoundingClientRect().right,
      shellHeight:shell.getBoundingClientRect().height,railHeight:rail.getBoundingClientRect().height,
      railScrollable:history.scrollHeight>history.clientHeight};
  });
  expect(Math.abs(geometry.gap)).toBeLessThanOrEqual(2);
  expect(Math.abs(geometry.shellHeight-geometry.railHeight)).toBeLessThanOrEqual(2);
  expect(geometry.railScrollable).toBe(true);
  const history=page.locator('.history');
  const details=page.locator('.console-scroll');
  await history.hover();
  const pageY=await page.evaluate(()=>window.scrollY);
  await page.mouse.wheel(0,600);
  await expect.poll(()=>history.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  expect(await details.evaluate(el=>el.scrollTop)).toBe(0);
  await details.hover();
  const toolbarY=(await page.locator('.toolbar').boundingBox())!.y;
  await page.mouse.wheel(0,700);
  await expect.poll(()=>details.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  expect((await page.locator('.toolbar').boundingBox())!.y).toBe(toolbarY);
  expect(await page.evaluate(()=>window.scrollY)).toBe(pageY);
  await expect(details.getByRole('heading',{name:'定时检查'})).toBeAttached();
  await page.screenshot({path:'../../.impeccable/review/console-unified-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(page.getByRole('button',{name:'开始检查 →'})).toBeVisible();
  await expect(page.locator('.history')).toHaveCount(0);
  await openNavigation(page);
  await expect(page.locator('.history')).toBeVisible();
  const firstRun=await page.locator('.run').nth(0).boundingBox();
  const secondRun=await page.locator('.run').nth(1).boundingBox();
  expect(secondRun!.y).toBeGreaterThan(firstRun!.y);
  expect(secondRun!.x).toBe(firstRun!.x);
  await page.locator('.run').first().click();
  await expect(page.locator('.history')).toHaveCount(0);
  await page.screenshot({path:'../../.impeccable/review/console-unified-mobile.png',fullPage:true});
});

test('Monitor 原有运行历史仍可调整宽度、收起和在手机打开',async({page})=>{
  await openWorkspace(page);
  await navigate(page,'Monitor 诊断');
  const before=await page.locator('.history').boundingBox();
  const grip=page.locator('.app-shell > .rail-resizer');
  await expect(grip).toBeVisible();
  const box=await grip.boundingBox();
  if(process.env.CHROME86_BIN){
    // Modern Playwright drag interception is absent in Chrome 86; send actual mouse input.
    const session=await page.context().newCDPSession(page);
    const x=box!.x+box!.width/2,y=box!.y+70;
    await session.send('Input.dispatchMouseEvent',{type:'mouseMoved',x,y});
    await session.send('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',buttons:1,clickCount:1});
    await session.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:x+65,y,button:'left',buttons:1});
    await session.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:x+65,y,button:'left',buttons:0,clickCount:1});
    await session.detach();
  }else{
    await page.mouse.move(box!.x+box!.width/2,box!.y+70);
    await page.mouse.down();
    await page.mouse.move(box!.x+box!.width/2+65,box!.y+70);
    await page.mouse.up();
  }
  await expect.poll(async()=>Math.round((await page.locator('.history').boundingBox())!.width)).toBeGreaterThan(Math.round(before!.width)+45);
  await page.getByRole('button',{name:'收起运行历史侧栏'}).click();
  await expect(page.locator('.history')).toHaveCount(0);
  await page.getByRole('button',{name:'展开运行历史侧栏'}).click();
  await expect(page.locator('.history')).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('.history')).toHaveCount(0);
  await openNavigation(page);
  await expect(page.locator('.history')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.history')).toHaveCount(0);
});
