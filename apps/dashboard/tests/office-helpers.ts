import {expect,type APIRequestContext,type Page} from '@playwright/test';

export async function loginForBrowser(request:APIRequestContext){
  const auth=await (await request.get('/api/v1/auth/me')).json();
  const response=await request.post('/api/v1/auth/login',{data:{username:'browser-admin',password:'browser-test-password'},headers:{Origin:'http://127.0.0.1:18001','X-CSRF-Token':auth.csrf_token}});
  expect(response.status()).toBe(200);
  return request.storageState();
}

export async function openWorkspace(page:Page){
  await page.goto('/');
  await expect(page.locator('.of-topbar h1')).toHaveText('智能体中心');
}

export async function openGeneral(page:Page){
  await openWorkspace(page);
  await page.locator('.of-resource-card').filter({has:page.getByRole('heading',{name:'通用助手',exact:true})}).getByRole('button',{name:'开始使用 ↗'}).click();
  await expect(page.getByRole('textbox',{name:'任务要求'})).toBeVisible();
}

// Chromium 86 predates the Browser.downloadWillBegin CDP event used by modern
// Playwright. Check the real downloaded file on disk for that browser instead.
export async function downloadArtifact(page:Page, selector:string){
  if(!process.env.CHROME86_BIN){
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator(selector).click()]);
    return download;
  }
  const {mkdtemp,readdir,readFile,rm}=await import('node:fs/promises');
  const {tmpdir}=await import('node:os');
  const {join}=await import('node:path');
  const directory=await mkdtemp(join(tmpdir(),'chromium86-download-'));
  const session=await page.context().newCDPSession(page);
  try{
    await session.send('Page.setDownloadBehavior',{behavior:'allow',downloadPath:directory});
    await page.locator(selector).click();
    let files:string[]=[];
    await expect.poll(async()=>{
      files=(await readdir(directory)).filter(name=>!name.endsWith('.crdownload'));
      return files.length;
    }).toBe(1);
    expect((await readFile(join(directory,files[0]))).length).toBeGreaterThan(0);
    return {suggestedFilename:()=>files[0],failure:async()=>null};
  }finally{await session.detach();await rm(directory,{recursive:true,force:true});}
}
