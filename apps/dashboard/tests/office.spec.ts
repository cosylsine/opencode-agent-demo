import {test,expect,type Page} from '@playwright/test';
import {loginForBrowser,downloadArtifact} from './office-helpers';

async function openTool(page: Page, title: string) {
  const card=page.locator('.of-tool-card').filter({has:page.getByRole('heading',{name:title,exact:true})});
  await card.getByRole('button',{name:'开始使用 ↗',exact:true}).click();
  await expect(page.getByRole('button',{name:'← 返回办公工具',exact:true})).toBeInViewport();
}


test('登录与自行注册进入智能体中心',async({page})=>{
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'登录工作台'})).toBeVisible();
  await page.getByRole('button',{name:'立即注册'}).click();
  await page.getByLabel('用户名',{exact:true}).fill('member-'+Date.now());
  await page.getByLabel('显示名称').fill('测试成员');
  await page.getByLabel('密码',{exact:true}).fill('member-test-password');
  await page.getByRole('button',{name:'注册并登录'}).click();
  await expect(page.locator('.of-topbar h1')).toHaveText('智能体中心');
  await expect(page.locator('.of-resource-card')).toHaveCount(4);
  await expect(page.getByRole('button',{name:'账号管理',exact:true})).toHaveCount(0);
  await page.screenshot({path:'../../docs/runbooks/office-center.png',fullPage:true});
  await page.getByRole('button',{name:'退出登录'}).click();
  await expect(page.getByRole('heading',{name:'登录工作台'})).toBeVisible();
});

test('工具中心直接计算并生成真实文档',async({page,request})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
  await page.goto('/');
  await page.locator('.of-sidebar nav').getByRole('button',{name:'办公工具',exact:true}).click();
  await openTool(page,'数值与统计计算');
  await page.getByLabel('计算表达式').fill('mean([2,4,6]) + 1');
  await page.getByRole('button',{name:'开始处理',exact:true}).click();
  await expect(page.locator('.of-number')).toHaveText('5');
  await page.getByRole('button',{name:'← 返回办公工具',exact:true}).click();
  await openTool(page,'生成 Word');
  await page.getByLabel('文档标题').fill('办公工具验收文档');
  await page.getByLabel('正文').fill('这是由实际文档生成工具创建的文件。');
  await page.getByRole('button',{name:'开始处理',exact:true}).click();
  await expect(page.locator('.of-file')).toBeVisible();
  const download=await downloadArtifact(page,'.of-file');
  expect(download.suggestedFilename()).toBe('生成文档.docx');
  expect(await download.failure()).toBeNull();
  await page.screenshot({path:'../../docs/runbooks/office-tools.png',fullPage:true});
});

test('新增工具搜索、文本去重、脱敏与工作日计算',async({page,request})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
  await page.goto('/#tools');
  await expect(page.locator('.of-tool-card')).toHaveCount(17);
  await page.getByRole('tab',{name:'文本与隐私',exact:true}).click();
  await expect(page.locator('.of-tool-card')).toHaveCount(4);
  await page.getByLabel('搜索办公工具').fill('没有这个工具');
  await expect(page.locator('.of-tool-card')).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('没有匹配的工具');
  await page.getByLabel('搜索办公工具').fill('去重');
  await openTool(page,'文本整理与统计');
  await page.getByLabel('文本内容',{exact:true}).fill('甲\n乙\n甲');
  await page.getByLabel('整理方式').selectOption('deduplicate');
  await page.getByRole('button',{name:'开始处理',exact:true}).click();
  await expect(page.locator('.of-execution')).toContainText('甲\n乙');
  await expect(page.locator('.of-file')).toBeVisible();
  await page.getByRole('button',{name:'← 返回办公工具',exact:true}).click();
  await expect(page.getByLabel('搜索办公工具')).toHaveValue('去重');
  await expect(page.getByRole('tab',{name:'文本与隐私',exact:true})).toHaveAttribute('aria-selected','true');
  await page.getByLabel('搜索办公工具').fill('脱敏');
  await openTool(page,'常见信息脱敏');
  await page.getByLabel('文本内容',{exact:true}).fill('联系人张三 13812345678 a@test.cn');
  await page.getByLabel('指定词语（可选）').fill('张三');
  await page.getByRole('button',{name:'开始处理',exact:true}).click();
  await expect(page.locator('.of-text-preview')).toContainText('[手机号已遮盖]');
  await expect(page.locator('.of-text-preview')).not.toContainText('13812345678');
  await page.getByRole('button',{name:'← 返回办公工具',exact:true}).click();
  await page.getByRole('tab',{name:'全部',exact:true}).click();
  await page.getByLabel('搜索办公工具').fill('工作日');
  await openTool(page,'工作日计算');
  await page.getByLabel('起始日期').fill('2026-10-19');
  await page.getByLabel('结束日期').fill('2026-10-25');
  await page.getByLabel('补班日期（可选）').fill('2026-10-24');
  await page.getByRole('button',{name:'开始处理',exact:true}).click();
  await expect(page.locator('.of-text-preview')).toContainText('5 个工作日');
});

test('批量工具上传、调整顺序和下载 ZIP，窄屏不溢出',async({page,request})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
  await page.setViewportSize({width:390,height:844});
  await page.goto('/#tools');
  await page.getByLabel('搜索办公工具').fill('打包');
  await openTool(page,'文件打包');
  await page.locator('input[type=file][multiple]').setInputFiles([
    {name:'甲.txt',mimeType:'text/plain',buffer:Buffer.from('甲内容')},
    {name:'乙.txt',mimeType:'text/plain',buffer:Buffer.from('乙内容')},
  ]);
  await expect(page.locator('.of-batch-files li')).toHaveCount(2);
  await expect(page.locator('.of-batch-files li').first()).toContainText('甲.txt');
  await page.getByRole('button',{name:'上移第 2 份文件'}).click();
  await expect(page.locator('.of-batch-files li').first()).toContainText('乙.txt');
  await page.getByRole('button',{name:'开始处理',exact:true}).click();
  await expect(page.locator('.of-text-preview')).toContainText('已打包 2 份文件');
  const download=await downloadArtifact(page,'.of-file');
  expect(download.suggestedFilename()).toBe('办公材料.zip');
  expect(await download.failure()).toBeNull();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('创建 Skill 和智能体，进入专属对话获取产物',async({page,request})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
  await page.goto('/');
  await page.locator('.of-sidebar nav').getByRole('button',{name:'Skill 中心',exact:true}).click();
  await page.getByRole('button',{name:'＋ 创建 Skill'}).click();
  const title='材料整理-'+Date.now();
  await page.getByLabel('名称',{exact:true}).fill(title);
  await page.getByLabel('用途',{exact:true}).fill('按照要求生成文字结果文件');
  await page.getByLabel('执行步骤',{exact:true}).fill('读取用户要求，生成 Markdown 文件。');
  await page.getByRole('button',{name:'选择工具',exact:true}).click();
  await page.getByLabel('搜索搭配工具').fill('导出文本');
  await page.locator('.of-picker-card').getByRole('checkbox',{name:/导出文本/}).check();
  await page.getByRole('button',{name:/确认选择/}).click();
  await page.getByRole('button',{name:'保存并发布'}).click();
  const card=page.locator('.of-resource-card').filter({hasText:title});
  await expect(card).toContainText('已发布 v1');
  await card.getByRole('button',{name:'创建智能体 ↗'}).click();
  await expect(page.getByLabel('名称',{exact:true})).toHaveValue(title+'助手');
  await page.getByRole('button',{name:'保存并发布'}).click();
  const agent=page.locator('.of-resource-card').filter({hasText:title+'助手'});
  await agent.getByRole('button',{name:'开始使用 ↗'}).click();
  await page.getByRole('textbox',{name:'任务要求'}).fill('请整理为一份检查清单');
  await page.getByRole('button',{name:'发送 ↑',exact:true}).click();
  await expect(page.locator('.of-message.assistant')).toContainText('测试驱动已完成任务');
  await expect(page.locator('.of-message.assistant .of-file')).toBeVisible();
  await page.getByRole('textbox',{name:'任务要求'}).fill('再生成一版');
  await page.getByRole('button',{name:'发送 ↑',exact:true}).click();
  await expect(page.locator('.of-message.assistant')).toHaveCount(2);
  await page.screenshot({path:'../../docs/runbooks/office-conversation.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('button',{name:'发送 ↑',exact:true})).toBeInViewport();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:'../../docs/runbooks/office-mobile.png',fullPage:true});
});

for(const width of [320,390,1024]){
  test(`工作台和工具界面无横向溢出 ${width}`,async({page,request})=>{
    await page.context().addCookies((await loginForBrowser(request)).cookies);
    await page.setViewportSize({width,height:844});
    await page.goto('/');
    await expect(page.locator('.of-topbar h1')).toHaveText('智能体中心');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.locator('.of-sidebar nav').getByRole('button',{name:'办公工具',exact:true}).click();
    await expect(page.locator('.of-tool-card')).toHaveCount(17);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await openTool(page,'PDF 合并与页面整理');
    await expect(page.getByRole('button',{name:'开始处理',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'← 返回办公工具',exact:true}).click();
    await expect(page.locator('.of-tool-card')).toHaveCount(17);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

test('业务应用沿用同一导航、账号和顶栏，切换不会回到旧版外壳',async({page,request})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
  await page.goto('/');
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});
    for(const title of ['Monitor 诊断','B 回路仿真调优','智能体中心']){
      await page.locator('.of-sidebar nav').getByRole('button',{name:title,exact:true}).click();
      await expect(page.locator('.of-topbar h1')).toHaveText(title);
      await expect(page.getByRole('navigation',{name:'工作区',exact:true})).toHaveCount(1);
      await expect(page.locator('.of-profile')).toBeVisible();
      await expect(page.locator('.of-legacy-bar, .brand-mark')).toHaveCount(0);
      await expect(page.locator('.of-sidebar nav [aria-current=page]')).toContainText(title);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      if(title==='B 回路仿真调优'){
        await expect(page.locator('.tuning-toolbar')).toHaveCSS('background-color','rgb(255, 255, 255)');
      }
    }
  }
});

test('通用助手开箱对话、多轮历史统一进入我的任务，旧接待入口只供查阅',async({page,request})=>{
  await page.context().addCookies((await loginForBrowser(request)).cookies);
  await page.goto('/');
  await expect(page.locator('.of-sidebar nav').getByRole('button',{name:'接待对话',exact:true})).toHaveCount(0);
  const general=page.locator('.of-resource-card').first();
  await expect(general.getByRole('heading')).toHaveText('通用助手');
  await general.getByRole('button',{name:'开始使用 ↗'}).click();
  await expect(page.locator('.of-topbar h1')).toHaveText('通用助手');
  const firstRequest='帮我整理一个简短会议议程-'+Date.now();
  await page.getByRole('textbox',{name:'任务要求'}).fill(firstRequest);
  await page.getByRole('button',{name:'发送 ↑',exact:true}).click();
  await expect(page.locator('.of-message.assistant')).toContainText('测试通用助手：帮我整理一个简短会议议程');
  await expect(page.locator('.of-message.assistant .of-file')).toHaveCount(0);
  const taskUrl=page.url();
  await page.reload();
  await expect(page.locator('.of-message')).toHaveCount(2);
  await page.getByRole('textbox',{name:'任务要求'}).fill('再补充一个讨论环节');
  await page.getByRole('button',{name:'发送 ↑',exact:true}).click();
  await expect(page.locator('.of-message.assistant')).toHaveCount(2);
  await page.locator('.of-sidebar nav').getByRole('button',{name:'我的任务',exact:true}).click();
  const task=page.locator('.of-resource-card').filter({hasText:firstRequest});
  await expect(task).toContainText('通用助手');
  await task.getByRole('button',{name:'继续任务 ↗'}).click();
  await expect(page).toHaveURL(taskUrl);
  await expect(page.locator('.of-message')).toHaveCount(4);
  await page.locator('.of-sidebar nav').getByRole('button',{name:'我的任务',exact:true}).click();
  await page.getByRole('button',{name:'旧版接待记录',exact:true}).click();
  await expect(page.locator('.of-topbar h1')).toHaveText('旧版接待记录');
  await expect(page.getByRole('textbox',{name:'任务要求'})).toHaveCount(0);
  await page.getByRole('button',{name:'新建通用对话',exact:true}).click();
  await expect(page.locator('.of-topbar h1')).toHaveText('通用助手');
  expect(page.url()).not.toBe(taskUrl);
});
