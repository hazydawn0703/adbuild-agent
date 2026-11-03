import {test,expect} from '@playwright/test';

test('task selection, questions and pending proposals survive refresh without execution',async({page})=>{
 const errors:string[]=[];const writes:string[]=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(/\/(execute|confirm)$/.test(new URL(r.url()).pathname)&&r.method()==='POST')writes.push(r.url())});
 const project={id:'p',name:'测试项目',tenant_id:'tenant',role:'admin',account_id:'account',policy:null,reserved:'0'};
 const first={id:'t1',name:'第一批',version:1,draft_id:null,questions:[],proposal:null,messages:[]};
 const second={id:'t2',name:'第二批',version:2,draft_id:null,questions:[{id:'q',field:'currency',question:'本批使用哪种币种？',requirement_ids:['r1','r2']}],proposal:null,messages:[{role:'assistant',content:'第二批待补充'}]};
 await page.addInitScript(()=>sessionStorage.setItem('adbuild-token','test-token'));
 await page.route('**/apps/adbuild-agent/api/**',async route=>{
  const path=new URL(route.request().url()).pathname.split('/api')[1];
  const payload=path==='/config'?{configured:true,execution_enabled:false}:path==='/projects'?[project]:path.endsWith('/workspace')?{project,tasks:[{id:first.id,data:first},{id:second.id,data:second}],files:[],drafts:[],runs:[],messages:[]}:path.endsWith('/connection')?{status:'disconnected',verified:false}:path==='/tasks/t1'?{task:first,draft:null,preview:null}:path==='/tasks/t2'?{task:second,draft:null,preview:null}:{};
  await route.fulfill({json:payload});
 });
 await page.goto('/apps/adbuild-agent');await page.waitForLoadState('networkidle');
 await expect(page.getByLabel('发送给 Agent 的需求')).toBeEnabled();
 await page.getByLabel('选择搭建任务').selectOption('t2');
 await expect(page.getByText('第二批待补充')).toBeVisible();
 await expect(page.getByLabel('本批使用哪种币种？')).toBeVisible();
 await page.reload();await page.waitForLoadState('networkidle');
 await expect(page.getByLabel('选择搭建任务')).toHaveValue('t2');
 await expect(page.getByText('第二批待补充')).toBeVisible();
 await expect(page.getByRole('button',{name:'搭建前预览与确认',exact:true})).toBeDisabled();
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 expect(errors).toEqual([]);expect(writes).toEqual([]);
});
