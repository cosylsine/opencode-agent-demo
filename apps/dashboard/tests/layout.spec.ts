import {test,expect,type Page} from '@playwright/test';
import {loginForBrowser,openGeneral} from './office-helpers';

test.beforeEach(async({page,request})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
});
async function fitsViewport(page:Page){
  const dimensions=await page.evaluate(()=>({height:innerHeight,width:innerWidth,
    pageHeight:document.documentElement.scrollHeight,pageWidth:document.documentElement.scrollWidth,
    scrollY:scrollY,main:document.querySelector('.of-main')!.getBoundingClientRect().toJSON(),
    content:document.querySelector('.of-content')!.getBoundingClientRect().toJSON()}));
  expect(dimensions.pageHeight).toBeLessThanOrEqual(dimensions.height);
  expect(dimensions.pageWidth).toBeLessThanOrEqual(dimensions.width);
  expect(dimensions.scrollY).toBe(0);
  expect(dimensions.main.bottom).toBeLessThanOrEqual(dimensions.height);
  expect(dimensions.content.height).toBeGreaterThan(40);
}
for(const viewport of [{width:1440,height:900},{width:1024,height:768},{width:390,height:844},{width:320,height:568},{width:844,height:390}]){
  test(`通用对话及业务页面不溢出 ${viewport.width}×${viewport.height}`,async({page})=>{
    await page.setViewportSize(viewport);
    await openGeneral(page);
    await fitsViewport(page);
    await expect(page.getByRole('button',{name:'发送 ↑',exact:true})).toBeInViewport();
    await page.getByRole('textbox',{name:'任务要求'}).fill('多行草稿\n'.repeat(60));
    await fitsViewport(page);
    for(const name of ['Monitor 诊断','B 回路仿真调优']){
      await page.locator('.of-sidebar nav').getByRole('button',{name,exact:true}).click();
      await fitsViewport(page);
      expect(await page.locator('.page-scroll, .console-scroll').evaluate(el=>el.scrollWidth-el.clientWidth)).toBeLessThanOrEqual(1);
    }
  });
}
for(const width of [1440,390]){
  test(`长任务消息独立滚动，侧栏切换不跳回最新 ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:844});
    const task={id:'long-general',title:'长对话回归',agent_id:'builtin-agent-general-assistant',agent_version:1,agent_title:'通用助手',greeting:'',examples:[],created_at:'2026-10-19T00:00:00Z'};
    const messages=Array.from({length:36},(_,i)=>({id:'message-'+i,role:i%2?'assistant':'user',content:`第 ${i+1} 条消息\n\n`+'长对话阅读位置。'.repeat(150),files:[],execution_id:null,created_at:task.created_at}));
    await page.route('**/api/v1/conversations',route=>route.fulfill({json:{schema_version:'1.0',items:[task]}}));
    await page.route('**/api/v1/conversations/long-general/messages',route=>route.fulfill({json:{schema_version:'1.0',items:messages}}));
    await page.route('**/api/v1/executions',route=>route.fulfill({json:[]}));
    await page.goto('/#work/'+task.id);
    await expect(page.locator('.of-message')).toHaveCount(36);
    const transcript=page.locator('.of-chat-scroll');
    await expect.poll(()=>transcript.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThan(2);
    await transcript.hover();
    await page.mouse.wheel(0,-1500);
    await expect(page.getByRole('button',{name:'↓ 回到最新消息'})).toBeVisible();
    await page.getByRole('button',{name:'收起主侧栏',exact:true}).click();
    await page.getByRole('button',{name:'展开主侧栏',exact:true}).click();
    await expect.poll(()=>transcript.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeGreaterThan(500);
    await fitsViewport(page);
    await page.getByRole('button',{name:'↓ 回到最新消息'}).click();
    await expect.poll(()=>transcript.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThan(2);
  });
}
test('通用对话中文选词及换行不误发，Ctrl+Enter 发送',async({page})=>{
  await openGeneral(page);
  const input=page.getByRole('textbox',{name:'任务要求'});
  await input.fill('输入法选词');
  let posts=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/conversations\/[^/]+\/messages$/.test(r.url()))posts++});
  await input.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true});
  await expect(input).toHaveValue('输入法选词');
  await input.press('End');
  await input.press('Shift+Enter');
  await input.press('a');
  await expect(input).toHaveValue('输入法选词\na');
  expect(posts).toBe(0);
  await input.press('Control+Enter');
  await expect(page.locator('.of-message.assistant')).toContainText('测试通用助手：输入法选词');
  expect(posts).toBe(1);
});
test('手机可搜索旧接待记录并查阅，页面仅发出读取请求',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  const items=Array.from({length:50},(_,i)=>({schema_version:'1.0',session_id:'old-'+i,title:'历史讨论 '+i,created_at:'2026-10-12T00:00:00Z'}));
  await page.route('**/api/v1/chat/sessions',route=>route.fulfill({json:{schema_version:'1.0',items}}));
  await page.route('**/api/v1/chat/sessions/*/messages',route=>route.fulfill({json:{schema_version:'1.0',items:[{schema_version:'1.0',message_id:'old-message',session_id:'old-49',role:'assistant',content:'原接待对话保留的内容',run_id:null,status:'complete',created_at:items[0].created_at}]}}));
  const writes:string[]=[];
  page.on('request',r=>{if(r.url().includes('/api/v1/')&&r.method()!=='GET')writes.push(r.method()+' '+r.url())});
  await page.goto('/#chat');
  await expect(page.locator('.of-topbar h1')).toHaveText('旧版接待记录');
  await page.getByRole('searchbox',{name:'搜索旧版对话'}).fill('历史讨论 49');
  await page.getByRole('combobox',{name:'选择旧版对话'}).selectOption('old-49');
  await expect(page.locator('.of-legacy-transcript')).toContainText('原接待对话保留的内容');
  await expect(page.locator('.of-legacy-transcript h3')).toHaveText('历史讨论 49');
  await expect(page.getByRole('textbox',{name:'任务要求'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'开始检查 →'})).toHaveCount(0);
  await fitsViewport(page);
  await page.setViewportSize({width:320,height:568});
  expect((await page.locator('.of-legacy-transcript').boundingBox())!.height).toBeGreaterThanOrEqual(320);
  expect(await page.locator('.of-content').evaluate(el=>el.scrollHeight-el.clientHeight)).toBeGreaterThan(100);
  expect(writes).toEqual([]);
});
