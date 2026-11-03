import {test,expect} from '@playwright/test';
import {createServer,Server} from 'node:http';
let backend:Server;
test.beforeAll(async()=>{backend=createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({role:'platform_admin'}))});await new Promise<void>(resolve=>backend.listen(8000,'127.0.0.1',resolve))});
test.afterAll(async()=>{await new Promise<void>(resolve=>backend.close(()=>resolve()))});
test('ops status, rename and permanent deletion confirmation',async({page,context},testInfo)=>{
 await context.addCookies([{name:'adbuild-ops-session',value:'ui-test',domain:'127.0.0.1',path:'/apps/adbuild-agent'}]);
 let tenant={id:'t',name:'测试空间',project_count:1,admins:[{id:'u',email:'first@test.invalid',active:true},{id:'v',email:'second@test.invalid',active:true}]};let deleted=false;
 const writes:{method:string;path:string;body:any}[]=[];
 await page.route('**/api/ops/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname.split('/api/ops')[1];
  if(req.method()==='GET')return route.fulfill({json:deleted?[]:[tenant]});
  const body=req.postData()?req.postDataJSON():null;writes.push({method:req.method(),path,body});
  if(path.endsWith('/status'))tenant.admins[0].active=body.active;
  if(path.endsWith('/name'))tenant.name=body.name;
  if(path.endsWith('/close'))deleted=true;
  await route.fulfill({json:{ok:true}});
 });
 await page.goto('/apps/adbuild-agent/ops');await page.waitForLoadState('networkidle');
 await page.getByRole('button',{name:'停用账号'}).first().click();await page.getByRole('button',{name:'确认停用',exact:true}).click();
 await expect(page.getByText('管理员状态已更新')).toBeVisible();expect(writes[0]).toEqual({method:'POST',path:'/tenants/t/admins/u/status',body:{active:false}});
 await page.getByRole('button',{name:'修改客户空间「测试空间」的名称'}).click();await page.getByRole('textbox',{name:'客户空间名称',exact:true}).fill('新空间');await page.getByRole('button',{name:'保存名称'}).click();await expect(page.getByRole('heading',{name:'新空间'})).toBeVisible();
 await page.getByRole('button',{name:'关闭空间',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await page.screenshot({path:testInfo.outputPath('ops-delete-desktop.png')});
 for(const [width,height] of [[1440,900],[390,844],[320,568],[667,375],[390,330]]){
  await page.setViewportSize({width,height});await expect(dialog.getByRole('button',{name:'永久删除',exact:true})).toBeInViewport();expect(await page.locator('body').evaluate(e=>e.scrollWidth>innerWidth)).toBe(false);
 }
 await page.getByRole('textbox',{name:'输入客户空间名称确认删除'}).fill('错误名称');await page.getByRole('button',{name:'永久删除',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('请输入完整');expect(writes.filter(w=>w.path.endsWith('/close'))).toHaveLength(0);
 await page.getByRole('textbox',{name:'输入客户空间名称确认删除'}).fill('新空间');await page.getByRole('button',{name:'永久删除',exact:true}).click();await expect(page.getByText('客户空间及其数据已永久删除')).toBeVisible();expect(writes.filter(w=>w.path.endsWith('/close'))).toHaveLength(1);
});

