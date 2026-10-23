import {test,expect} from '@playwright/test';

for (const missingAPIs of [false,true]) {
  test(`内网兼容：登录、Markdown、多轮、重载和工作区，无外部请求 missingAPIs=${missingAPIs}`, async({page})=>{
    const errors:string[]=[];
    const external:string[]=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{
      if(!request.url().startsWith('http://127.0.0.1:18001/')&&!request.url().startsWith('data:'))external.push(request.url());
    });
    if(missingAPIs)await page.addInitScript(()=>{
      Object.defineProperty(Array.prototype,'at',{value:undefined,configurable:true,writable:true});
      Object.defineProperty(Object,'hasOwn',{value:undefined,configurable:true,writable:true});
      Object.defineProperty(globalThis,'structuredClone',{value:undefined,configurable:true});
      Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true});
    });
    await page.goto('/');
    await page.getByLabel('用户名',{exact:true}).fill('browser-admin');
    await page.getByLabel('密码',{exact:true}).fill('browser-test-password');
    await page.getByRole('button',{name:'登录',exact:true}).click();
    await expect(page.locator('.of-topbar h1')).toHaveText('智能体中心');
    await page.locator('.of-resource-card').filter({has:page.getByRole('heading',{name:'通用助手',exact:true})}).getByRole('button',{name:'开始使用 ↗'}).click();
    await page.getByRole('textbox',{name:'任务要求'}).fill('**旧浏览器兼容测试**');
    await page.getByRole('button',{name:'发送 ↑',exact:true}).click();
    await expect(page.locator('.of-message.assistant strong').filter({hasText:'旧浏览器兼容测试'})).toBeVisible();
    await page.reload();
    await expect(page.locator('.of-message.assistant strong').filter({hasText:'旧浏览器兼容测试'})).toBeVisible();
    await page.getByRole('textbox',{name:'任务要求'}).fill('第二轮继续');
    await page.getByRole('button',{name:'发送 ↑',exact:true}).click();
    await expect(page.locator('.of-message.assistant')).toHaveCount(2);
    for(const name of ['Monitor 诊断','B 回路仿真调优','ST 代码优化','办公工具','Skill 中心','智能体中心']){
      await page.locator('.of-sidebar nav').getByRole('button',{name,exact:true}).click();
      await expect(page.locator('.of-topbar h1')).toHaveText(name);
    }
    expect(errors).toEqual([]);
    expect(external).toEqual([]);
  });
}
