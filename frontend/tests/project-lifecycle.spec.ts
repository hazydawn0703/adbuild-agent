import {test,expect} from '@playwright/test';

for(const width of [1280,390,320])test(`project deletion and task archive at ${width}px`,async({page})=>{
 test.setTimeout(90000);
 await page.setViewportSize({width,height:800});
 const project={id:'p',name:'测试项目',tenant_id:'t',tenant_name:'空间',role:'admin',account_id:'1',policy:null};
 let deleted=false;
 let task={id:'task',name:'测试任务',version:1,draft_id:null,archived_at:null as string|null,messages:[],questions:[],proposal:null};
 await page.addInitScript(()=>sessionStorage.setItem('adbuild-token','test'));
 await page.route('**/apps/adbuild-agent/api/**',async route=>{
  const path=new URL(route.request().url()).pathname.split('/api')[1];
  let result:unknown={};
  if(path==='/config')result={configured:true};
  if(path==='/projects')result=deleted?[]:[project];
  if(path==='/projects/p/workspace')result={project,tasks:[{id:task.id,data:task}],files:[],drafts:[],runs:[],messages:[]};
  if(path==='/tasks/task')result={task,draft:null};
  if(path==='/projects/p/connection')result={status:'disconnected',verified:false};
  if(path==='/tasks/task/archive'){const body=route.request().postDataJSON();expect(body.expected_version).toBe(task.version);task={...task,version:task.version+1,archived_at:body.archived?'2026-11-03T00:00:00Z':null};result=task;}
  if(path==='/projects/p'&&route.request().method()==='DELETE'){deleted=true;result={deleted:true};}
  await route.fulfill({json:result});
 });
 await page.goto('/apps/adbuild-agent',{waitUntil:'domcontentloaded'});
 await page.getByRole('button',{name:'归档任务',exact:true}).click();
 await page.getByRole('button',{name:'确认归档',exact:true}).click();
 await expect(page.getByLabel('选择搭建任务')).toHaveValue('');
 await page.getByRole('button',{name:'查看已归档任务'}).click();
 await expect(page.getByLabel('选择搭建任务')).toHaveValue('task');
 await page.getByRole('button',{name:'恢复任务',exact:true}).click();
 await page.getByRole('button',{name:'确认恢复'}).click();
 await page.getByRole('button',{name:'查看进行中的任务'}).click();
 await expect(page.getByLabel('选择搭建任务')).toHaveValue('task');
 await page.getByRole('button',{name:'删除项目',exact:true}).click();
 await page.getByRole('button',{name:'取消',exact:true}).click();
 expect(deleted).toBe(false);
 await page.getByRole('button',{name:'删除项目',exact:true}).click();
 await page.getByRole('button',{name:'永久删除'}).click();
 await expect(page.getByLabel('选择搭建任务')).toHaveValue('');
 expect(deleted).toBe(true);
});
