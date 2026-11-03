import {test,expect} from '@playwright/test';

for(const width of [1440,390,320])test(`task rename preserves selection, version and input at ${width}px`,async({page})=>{
 test.setTimeout(90000);
 await page.setViewportSize({width,height:844});
 let task={id:'task',name:'十一月第一批',version:3,draft_id:null,archived_at:null as string|null,messages:[],questions:[],proposal:null};
 let conflict=true;
 const writes:unknown[]=[];
 const project={id:'p',name:'测试项目',tenant_id:'t',role:'admin',account_id:'1',policy:null};
 await page.addInitScript(()=>sessionStorage.setItem('adbuild-token','test'));
 await page.route('**/apps/adbuild-agent/api/**',async route=>{
  const path=new URL(route.request().url()).pathname.split('/api')[1];
  if(route.request().method()==='PATCH'){
   expect(path).toBe('/tasks/task');
   const body=route.request().postDataJSON();writes.push(body);
   expect(body).toEqual({name:'感恩节新增素材批次',expected_version:3});
   if(conflict){conflict=false;return route.fulfill({status:409,json:{detail:'任务已变化，请刷新后重试'}})}
   task={...task,name:body.name,version:4};return route.fulfill({json:{id:task.id,name:task.name,version:4}});
  }
  const payload=path==='/config'?{configured:true}:path==='/projects'?[project]:path.endsWith('/workspace')?{project,tasks:[{id:task.id,data:task}],files:[],drafts:[],runs:[],messages:[]}:path==='/tasks/task'?{task,draft:null}:{status:'disconnected',verified:false};
  return route.fulfill({json:payload});
 });
 await page.goto('/apps/adbuild-agent');await page.waitForLoadState('networkidle');
 const trigger=page.getByRole('button',{name:'重命名搭建任务',exact:true});
 await expect(page.getByLabel('选择搭建任务')).toHaveValue('task',{timeout:20000});await expect(trigger).toBeEnabled();await expect(trigger.locator('svg')).toHaveCount(1);
 await trigger.click();
 const dialog=page.getByRole('dialog',{name:'重命名搭建任务',exact:true});
 await expect(dialog.getByLabel('任务名称')).toHaveValue('十一月第一批');
 await dialog.getByLabel('任务名称').fill('取消不会保存');await page.keyboard.press('Escape');expect(writes).toHaveLength(0);
 await expect(trigger).toBeFocused();
 await trigger.click();await dialog.getByLabel('任务名称').fill('   ');await expect(dialog.getByRole('button',{name:'保存名称'})).toBeDisabled();
 await dialog.getByLabel('任务名称').fill('感恩节新增素材批次');await dialog.getByRole('button',{name:'保存名称'}).click();
 await expect(dialog.getByRole('alert')).toContainText('任务已变化');
 await expect(dialog.getByLabel('任务名称')).toHaveValue('感恩节新增素材批次');
 await dialog.getByRole('button',{name:'保存名称'}).click();await expect(dialog).toBeHidden();
 await expect(page.getByLabel('选择搭建任务')).toHaveValue('task');
 await expect(page.getByLabel('选择搭建任务').locator('option:checked')).toHaveText('感恩节新增素材批次',{timeout:20000});
 await page.reload();await page.waitForLoadState('networkidle');
 await expect(page.getByLabel('选择搭建任务').locator('option:checked')).toHaveText('感恩节新增素材批次',{timeout:20000});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 expect(writes).toHaveLength(2);
 project.role='viewer';await page.reload();await page.waitForLoadState('networkidle');await expect(page.getByLabel('选择搭建任务')).toHaveValue('task',{timeout:20000});await expect(trigger).toBeDisabled();
 project.role='admin';task.archived_at='2026-11-03T00:00:00Z';await page.reload();await page.waitForLoadState('networkidle');await page.getByRole('button',{name:'测试项目的项目菜单'}).click();await page.getByRole('button',{name:'已归档搭建任务',exact:true}).click();await page.getByRole('dialog',{name:'已归档搭建任务',exact:true}).getByRole('button',{name:'查看任务'}).click();await expect(page.getByLabel('选择搭建任务')).toHaveValue('task');await expect(trigger).toBeDisabled();
});

