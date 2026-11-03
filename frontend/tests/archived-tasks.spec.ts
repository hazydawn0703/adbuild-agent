import {test,expect} from '@playwright/test';

test('archived dialog scopes to the menu project and opens read-only tasks',async({page})=>{
 test.setTimeout(90000);
 const projects=[{id:'p',name:'当前项目',role:'admin',tenant_id:'t',account_id:'1',policy:null},{id:'other',name:'其他项目',role:'viewer',tenant_id:'other',account_id:'2',policy:null}];
 const active={id:'active',name:'进行中的任务',version:1,draft_id:null,archived_at:null,messages:[],questions:[],proposal:null};
 const archived={...active,id:'archived',name:'已归档的长名称任务'.repeat(12),archived_at:'2026-11-03T00:00:00Z'};
 const writes:string[]=[];
 await page.addInitScript(()=>{sessionStorage.setItem('adbuild-token','test');sessionStorage.setItem('adbuild-project','p')});
 await page.route('**/apps/adbuild-agent/api/**',async route=>{
  const path=new URL(route.request().url()).pathname.split('/api')[1];
  if(route.request().method()!=='GET')writes.push(path);
  const project=path.includes('/projects/other/')?projects[1]:projects[0];
  const tasks=project.id==='other'?[archived]:[active];
  await route.fulfill({json:path==='/config'?{configured:true}:path==='/projects'?projects:path.endsWith('/workspace')?{project,tasks:tasks.map(t=>({id:t.id,data:t})),drafts:[],files:[],runs:[],messages:[]}:path==='/tasks/archived'?{task:archived,draft:null}:path==='/tasks/active'?{task:active,draft:null}:{status:'disconnected',verified:false}});
 });
 await page.goto('/apps/adbuild-agent');await page.waitForLoadState('networkidle');
 await expect(page.locator('.center').getByRole('button',{name:'查看已归档任务'})).toHaveCount(0);
 await page.getByRole('button',{name:'当前项目的项目菜单'}).click();await page.getByRole('button',{name:'已归档搭建任务',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'已归档搭建任务',exact:true});
 await expect(dialog).toContainText('暂无已归档搭建任务');await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'其他项目的项目菜单'}).click();await page.getByRole('button',{name:'已归档搭建任务',exact:true}).click();
 await expect(dialog).toContainText(archived.name);await expect(page.locator('.project.selected')).toHaveText('当前项目');
 await expect(dialog.getByRole('button',{name:'恢复任务',exact:true})).toBeDisabled();
 for(const size of [{width:1440,height:900},{width:390,height:844},{width:320,height:568},{width:667,height:375},{width:390,height:330}]){
  await page.setViewportSize(size);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(await dialog.locator('.modal-body').evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBeTruthy();
  await expect(dialog.getByRole('button',{name:'关闭',exact:true})).toBeInViewport();
 }
 await dialog.getByRole('button',{name:'查看任务'}).click();
 await expect(dialog).toBeHidden();await expect(page.locator('.project.selected')).toHaveText('其他项目');
 await expect(page.getByLabel('选择搭建任务')).toHaveValue('archived');await expect(page.getByRole('button',{name:'重命名搭建任务'})).toBeDisabled();
 expect(writes).toEqual([]);
});

