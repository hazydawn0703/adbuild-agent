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
 await page.getByRole('button',{name:'测试项目的项目菜单'}).click();
 await page.getByRole('button',{name:'删除项目',exact:true}).click();
 await page.getByRole('button',{name:'取消',exact:true}).click();
 expect(deleted).toBe(false);
 await page.getByRole('button',{name:'测试项目的项目菜单'}).click();
 await page.getByRole('button',{name:'删除项目',exact:true}).click();
 await page.getByRole('button',{name:'永久删除'}).click();
 await expect(page.getByLabel('选择搭建任务')).toHaveValue('');
 expect(deleted).toBe(true);
});


test('project menu targets its own row and toolbar actions fit all screen sizes',async({page})=>{
 test.setTimeout(90000);
 let projects=[{id:'p',name:'当前项目',tenant_id:'t',tenant_name:'空间',role:'admin',account_id:'1',policy:null},{id:'other',name:'另一项目',tenant_id:'t',tenant_name:'空间',role:'admin',account_id:'1',policy:null}];
 let conflict=true;
 const deletes:string[]=[];
 await page.addInitScript(()=>{sessionStorage.setItem('adbuild-token','test');sessionStorage.setItem('adbuild-project','p')});
 await page.route('**/apps/adbuild-agent/api/**',async route=>{
  const path=new URL(route.request().url()).pathname.split('/api')[1];
  if(route.request().method()==='PATCH'){
   expect(path).toBe('/projects/other');
   expect(route.request().postDataJSON()).toEqual({name:'新的项目名称',expected_name:'另一项目'});
   if(conflict){conflict=false;return route.fulfill({status:409,json:{detail:'项目名称已变化，请刷新后重试'}})}
   projects=projects.map(p=>p.id==='other'?{...p,name:'新的项目名称'}:p);
   return route.fulfill({json:projects[1]});
  }
  if(route.request().method()==='DELETE'){deletes.push(path);projects=projects.filter(p=>p.id!=='other');return route.fulfill({json:{deleted:true}})}
  return route.fulfill({json:path==='/config'?{configured:true}:path==='/projects'?projects:path.endsWith('/workspace')?{project:projects[0],tasks:[],files:[],drafts:[],runs:[],messages:[]}:{status:'disconnected',verified:false}});
 });
 await page.goto('/apps/adbuild-agent',{waitUntil:'domcontentloaded'});
 await page.getByRole('button',{name:'另一项目的项目菜单'}).click();
 await page.keyboard.press('Escape');
 await expect(page.getByRole('button',{name:'重命名',exact:true})).toBeHidden();
 await page.getByRole('button',{name:'另一项目的项目菜单'}).click();
 await page.getByRole('button',{name:'重命名',exact:true}).click();
 await page.getByRole('textbox',{name:'项目名称',exact:true}).fill('新的项目名称');
 await page.getByRole('button',{name:'保存名称'}).click();
 await expect(page.getByRole('dialog').getByRole('alert')).toContainText('项目名称已变化');
 await expect(page.getByRole('textbox',{name:'项目名称',exact:true})).toHaveValue('新的项目名称');
 await page.getByRole('button',{name:'保存名称'}).click();
 await expect(page.locator('.project.selected')).toHaveText('当前项目');
 await expect(page.locator('.project-list')).toContainText('新的项目名称');
 await expect(page.locator('.center').getByRole('button',{name:'删除项目',exact:true})).toHaveCount(0);
 const topbar=page.locator('.topbar');
 expect(await topbar.locator('.outline').allTextContents()).toEqual(['项目默认配置','项目授权']);
 expect(await page.locator('.task-actions button').allTextContents()).toEqual(['查看已归档任务','归档任务','新建搭建任务']);
 for(const width of [1440,1280,1024,390,320]){
  await page.setViewportSize({width,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.getByRole('button',{name:'新的项目名称的项目菜单'}).click();
  const menu=page.getByLabel('新的项目名称的项目操作',{exact:true});
  await expect(menu).toBeVisible();
  if(width===1440||width===320)await page.screenshot({path:`../artifacts/project-menu-${width}.png`});
  const r=(await menu.boundingBox())!;expect(r.x).toBeGreaterThanOrEqual(0);expect(r.x+r.width).toBeLessThanOrEqual(width);
  await page.keyboard.press('Escape');
 }
 await page.getByRole('button',{name:'新的项目名称的项目菜单'}).click();
 await page.getByRole('button',{name:'删除项目',exact:true}).click();
 await expect(page.getByRole('dialog')).toContainText('新的项目名称');
 await page.getByRole('button',{name:'永久删除'}).click();
 expect(deletes).toEqual(['/projects/other']);
 await expect(page.locator('.project.selected')).toHaveText('当前项目');
});
