import {test,expect,type APIRequestContext} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {loginForBrowser} from './office-helpers';

async function write(request: APIRequestContext, method: string, path: string, data?: unknown) {
  const auth=await (await request.get('/api/v1/auth/me')).json();
  return request.fetch('/api/v1'+path,{method,data,headers:{Origin:'http://127.0.0.1:18001','X-CSRF-Token':auth.csrf_token,'Idempotency-Key':randomUUID()}});
}

test('创建时选择公共、修改权限、归档和删除，旧任务保留为只读',async({page,request,browser})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
  const member=await browser.newContext({baseURL:'http://127.0.0.1:18001'});
  const memberCredentials={username:'resource-member-'+Date.now(),password:'resource-member-password'};
  const registered=await write(member.request,'POST','/auth/register',{...memberCredentials,display_name:'资源测试成员'});
  expect(registered.status()).toBe(201);
  expect((await write(member.request,'POST','/auth/login',memberCredentials)).status()).toBe(200);
  const name='权限与删除验收-'+Date.now();
  let skillId='',agentId='';
  try {
    await page.goto('/#skills');
    await page.getByRole('button',{name:'＋ 创建 Skill',exact:true}).click();
    await page.getByLabel('名称',{exact:true}).fill(name);
    await page.getByLabel('用途',{exact:true}).fill('导出文字文件');
    await page.getByLabel('可见范围',{exact:true}).selectOption('public');
    await page.getByLabel('执行步骤',{exact:true}).fill('根据用户要求导出文本文件。');
    await page.getByRole('button',{name:'选择工具',exact:true}).click();
    await page.getByLabel('搜索搭配工具').fill('导出文本');
    await page.locator('.of-picker-card').getByRole('checkbox',{name:/导出文本/}).check();
    await page.getByRole('button',{name:/确认选择/}).click();
    const skillResponse=page.waitForResponse(r=>r.url().endsWith('/api/v1/skills')&&r.request().method()==='POST');
    await page.getByRole('button',{name:'保存并发布',exact:true}).click();
    const skill=await (await skillResponse).json();skillId=skill.id;
    expect(skill.visibility).toBe('public');expect(skill.published_version).toBe(1);
    expect((await member.request.get('/api/v1/skills/'+skillId)).status()).toBe(200);
    await page.locator('.of-resource-card').filter({has:page.getByRole('heading',{name,exact:true})}).getByRole('button',{name:'创建智能体 ↗'}).click();
    await page.getByLabel('可见范围',{exact:true}).selectOption('public');
    const agentResponse=page.waitForResponse(r=>r.url().endsWith('/api/v1/agents')&&r.request().method()==='POST');
    await page.getByRole('button',{name:'保存并发布',exact:true}).click();
    const agent=await (await agentResponse).json();agentId=agent.id;
    expect(agent.visibility).toBe('public');expect(agent.published_version).toBe(1);
    expect((await member.request.get('/api/v1/agents/'+agentId)).status()).toBe(200);
    await page.locator('.of-resource-detail').getByRole('button',{name:'编辑草稿',exact:true}).click();
    await page.getByLabel('可见范围',{exact:true}).selectOption('private');
    await page.getByRole('button',{name:'保存草稿',exact:true}).click();
    await expect(page.locator('.of-resource-detail')).toContainText('仅自己可见');
    expect((await member.request.get('/api/v1/agents/'+agentId)).status()).toBe(404);
    await page.locator('.of-resource-detail').getByRole('button',{name:'设为公共',exact:true}).click();
    await expect(page.locator('.of-resource-detail')).toContainText('公共');
    const agentCard=page.locator('.of-resource-card').filter({has:page.getByRole('heading',{name:name+'助手',exact:true})});
    await agentCard.getByRole('button',{name:'开始使用 ↗'}).click();
    await page.getByRole('textbox',{name:'任务要求'}).fill('生成一份需要保留的文字结果');
    await page.getByRole('button',{name:'发送 ↑',exact:true}).click();
    await expect(page.locator('.of-message.assistant .of-file')).toBeVisible();
    const taskUrl=page.url();
    await page.locator('.of-sidebar nav').getByRole('button',{name:'智能体中心',exact:true}).click();
    await agentCard.getByRole('button',{name:'查看详情',exact:true}).click();
    await page.locator('.of-resource-detail').getByRole('button',{name:'归档资源',exact:true}).click();
    await expect(page.locator('.of-resource-detail')).toContainText('已归档');
    expect((await member.request.get('/api/v1/agents/'+agentId)).status()).toBe(404);
    await page.locator('.of-resource-detail').getByRole('button',{name:'恢复资源',exact:true}).click();
    await expect(page.locator('.of-resource-detail').getByRole('button',{name:'归档资源',exact:true})).toBeVisible();
    expect((await member.request.get('/api/v1/agents/'+agentId)).status()).toBe(200);
    await page.locator('.of-resource-detail').getByRole('button',{name:'删除资源',exact:true}).click();
    await page.getByRole('dialog',{name:'删除资源',exact:true}).getByRole('button',{name:'确认删除',exact:true}).click();
    await expect(page.getByRole('dialog',{name:'删除资源',exact:true})).toHaveCount(0);
    expect((await member.request.get('/api/v1/agents/'+agentId)).status()).toBe(404);
    await page.locator('.of-sidebar nav').getByRole('button',{name:'我的任务',exact:true}).click();
    await page.locator('.of-resource-card').filter({hasText:'生成一份需要保留的文字结果'}).first().getByRole('button',{name:'继续任务 ↗'}).click();
    await expect(page).toHaveURL(taskUrl);
    await page.reload();
    await expect(page.locator('.of-message')).toHaveCount(2);
    await expect(page.locator('.of-message.assistant .of-file')).toBeVisible();
    await expect(page.getByRole('textbox',{name:'任务要求'})).toBeDisabled();
    await expect(page.locator('.of-composer')).toContainText('历史消息和文件仍可查阅');
    await page.locator('.of-sidebar nav').getByRole('button',{name:'Skill 中心',exact:true}).click();
    await page.locator('.of-resource-card').filter({has:page.getByRole('heading',{name,exact:true})}).getByRole('button',{name:'查看详情',exact:true}).click();
    await page.locator('.of-resource-detail').getByRole('button',{name:'删除资源',exact:true}).click();
    await page.getByRole('dialog',{name:'删除资源',exact:true}).getByRole('button',{name:'确认删除',exact:true}).click();
    await expect(page.getByRole('dialog',{name:'删除资源',exact:true})).toHaveCount(0);
    expect((await member.request.get('/api/v1/skills/'+skillId)).status()).toBe(404);
  } finally {
    if(agentId) await write(request,'DELETE','/agents/'+agentId);
    if(skillId) await write(request,'DELETE','/skills/'+skillId);
    await member.close();
  }
});

for (const viewport of [{width:1440,height:700},{width:1024,height:600},{width:390,height:844},{width:320,height:568},{width:844,height:390}]) {
  test(`编辑及能力选择标题栏滚动后贴住工作区顶部，字段不被遮挡 ${viewport.width}×${viewport.height}`,async({page,request})=>{
    await page.context().addCookies((await loginForBrowser(request)).cookies);
    await page.setViewportSize(viewport);
    const skills=await (await request.get('/api/v1/skills')).json();
    const template=skills.items.find((item: {published_version:number})=>item.published_version>0);
    await page.route('**/api/v1/skills',route=>route.fulfill({json:{...skills,items:Array.from({length:8},(_,i)=>({...template,id:'header-scroll-skill-'+i,content:{...template.content,title:'材料整理 Skill '+i,description:'用于检查材料完整性并汇总数据，支持多个字段和统计口径。'.repeat(3)}}))}}));
    await page.goto('/#agents');
    const main=page.locator('.of-content');
    async function headingIsFlush(selector:string){
      await main.evaluate(e=>{e.scrollTop=160;});
      expect(await main.evaluate(e=>e.scrollTop)).toBeGreaterThan(20);
      const position=await page.locator(selector).evaluate(header=>{
        const parent=header.closest('.of-content')!;
        const h=header.getBoundingClientRect(),p=parent.getBoundingClientRect();
        return {gap:h.top-p.top,headerCoversTop:header.contains(document.elementFromPoint(h.left+10,p.top+1))};
      });
      expect(Math.abs(position.gap)).toBeLessThan(1);
      expect(position.headerCoversTop).toBe(true);
    }
    async function fieldIsClear(label:string){
      const field=page.getByLabel(label,{exact:true});
      await field.evaluate(e=>e.scrollIntoView({block:'start'}));
      const bottom=await page.locator('.of-editor-heading').evaluate(e=>e.getBoundingClientRect().bottom);
      expect(await field.evaluate(e=>e.getBoundingClientRect().top)).toBeGreaterThanOrEqual(bottom);
    }
    await page.getByRole('button',{name:'＋ 创建智能体',exact:true}).click();
    await headingIsFlush('.of-editor-heading');
    await fieldIsClear('用途');
    await page.getByRole('button',{name:'选择 Skill',exact:true}).click();
    await headingIsFlush('.of-picker-heading');
    await page.getByRole('button',{name:'取消选择',exact:true}).click();
    await page.getByRole('button',{name:'选择工具',exact:true}).click();
    await headingIsFlush('.of-picker-heading');
    await page.getByRole('button',{name:'取消选择',exact:true}).click();
    await page.getByRole('button',{name:'返回列表',exact:true}).click();
    await expect(main).not.toHaveClass(/of-editor-content/);
    expect(await main.evaluate(e=>e.style.getPropertyValue('--of-editor-heading-height'))).toBe('');
    await page.locator('.of-sidebar nav').getByRole('button',{name:'Skill 中心',exact:true}).click();
    await page.getByRole('button',{name:'＋ 创建 Skill',exact:true}).click();
    await headingIsFlush('.of-editor-heading');
    await fieldIsClear('执行步骤');
    await page.getByRole('button',{name:'选择工具',exact:true}).click();
    await headingIsFlush('.of-picker-heading');
    expect(await main.evaluate(e=>e.scrollWidth-e.clientWidth)).toBeLessThanOrEqual(1);
  });
}

test('编辑和能力选择共用主滚动区域，草稿、分页选择和返回位置保留',async({page,request})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
  const main=page.locator('.of-content');
  async function noNestedScroll(){
    const nested=await main.evaluate(root=>Array.from(root.querySelectorAll('*')).filter(e=>e.clientHeight>0 && e.scrollHeight>e.clientHeight+2 && ['auto','scroll'].includes(getComputedStyle(e).overflowY)).map(e=>e.className));
    expect(nested).toEqual([]);
  }
  for(const viewport of [{width:1440,height:900},{width:1024,height:768},{width:390,height:844},{width:320,height:568},{width:844,height:390}]){
    await page.setViewportSize(viewport);
    await page.goto('/#skills');
    await page.getByRole('button',{name:'＋ 创建 Skill',exact:true}).click();
    await page.getByLabel('名称',{exact:true}).fill('需要保留的草稿');
    await page.getByLabel('用途',{exact:true}).fill('选择能力时不丢失输入内容');
    const instructions='这是一段较长的执行步骤，用来验证正文随内容增高。\n'.repeat(45);
    const body=page.getByLabel('执行步骤',{exact:true});
    await body.fill(instructions);
    expect(await body.evaluate(e=>e.scrollHeight-e.clientHeight)).toBeLessThanOrEqual(2);
    await noNestedScroll();
    const open=page.getByRole('button',{name:'选择工具',exact:true});
    await open.click();
    await expect(page.getByRole('heading',{name:'选择工具',exact:true})).toBeFocused();
    await expect(page.locator('.of-editor-form')).toHaveCount(0);
    await page.getByLabel('搜索搭配工具').fill('生成 Word');
    await page.locator('.of-picker-card').getByRole('checkbox',{name:/生成 Word/}).check();
    await page.getByRole('button',{name:'取消选择',exact:true}).click();
    await expect(page.getByLabel('名称',{exact:true})).toHaveValue('需要保留的草稿');
    await expect(body).toHaveValue(instructions);
    await expect(page.locator('.of-config-tool')).toHaveCount(0);
    await open.scrollIntoViewIfNeeded();
    const before=await main.evaluate(e=>e.scrollTop);
    await open.click();
    await expect.poll(()=>main.evaluate(e=>e.scrollTop)).toBe(0);
    await page.locator('.of-picker-card').getByRole('checkbox',{name:/生成 Word/}).check();
    await page.getByRole('button',{name:'下一页',exact:true}).click();
    await page.locator('.of-picker-card').getByRole('checkbox',{name:/导出文本/}).check();
    await page.getByRole('checkbox',{name:/仅看已选/}).check();
    await expect(page.locator('.of-picker-card')).toHaveCount(2);
    await expect(page.locator('.of-picker-card input:checked')).toHaveCount(2);
    await noNestedScroll();
    const card=page.locator('.of-picker-card').last();
    await card.hover();
    const scroll=await main.evaluate(e=>({top:e.scrollTop,max:e.scrollHeight-e.clientHeight}));
    if(scroll.max-scroll.top>40){
      await page.mouse.wheel(0,100);
      await expect.poll(()=>main.evaluate(e=>e.scrollTop)).toBeGreaterThan(scroll.top);
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    await page.getByRole('button',{name:/确认选择/}).click();
    await expect(open).toBeFocused();
    const restored=await main.evaluate(e=>({top:e.scrollTop,max:e.scrollHeight-e.clientHeight}));
    expect(Math.abs(restored.top-Math.min(before,restored.max))).toBeLessThan(3);
    await expect(body).toHaveValue(instructions);
    await expect(page.locator('.of-config-tool')).toHaveCount(2);
    await noNestedScroll();
    await page.getByRole('button',{name:'返回列表',exact:true}).click();
  }
  await page.locator('.of-sidebar nav').getByRole('button',{name:'智能体中心',exact:true}).click();
  await page.getByRole('button',{name:'＋ 创建智能体',exact:true}).click();
  await page.getByRole('button',{name:'选择 Skill',exact:true}).click();
  await page.getByLabel('搜索可选 Skill').fill('表格汇总');
  await page.locator('.of-picker-card').getByRole('checkbox',{name:/表格汇总/}).check();
  await page.getByRole('button',{name:/确认选择/}).click();
  await expect(page.locator('.of-config-skill')).toContainText('表格汇总');
  await expect(page.locator('.of-config-tool')).toHaveCount(4);
  await page.getByRole('button',{name:'选择工具',exact:true}).click();
  await page.getByLabel('搜索搭配工具').fill('读取表格');
  await expect(page.locator('.of-picker-card').getByRole('checkbox')).toBeChecked();
  await expect(page.locator('.of-picker-card').getByRole('checkbox')).toBeDisabled();
  await page.getByRole('button',{name:'取消选择',exact:true}).click();
  await page.getByRole('button',{name:'移除 Skill：表格汇总',exact:true}).click();
  await expect(page.locator('.of-config-tool')).toHaveCount(0);
});
