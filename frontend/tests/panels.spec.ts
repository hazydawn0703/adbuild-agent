import {test,expect,Page} from '@playwright/test';

async function workspace(page:Page) {
  const project={id:'p',name:'侧栏交互测试项目',tenant_id:'tenant',role:'admin',account_id:'account',policy:null,reserved:'0'};
  const task={id:'t',name:'当前搭建任务',version:1,draft_id:null,questions:[],messages:[],proposal:null};
  await page.addInitScript(()=>sessionStorage.setItem('adbuild-token','test-token'));
  await page.route('**/apps/adbuild-agent/api/**',async route=>{
    const path=new URL(route.request().url()).pathname.split('/api')[1];
    const payload=path==='/config'?{configured:true,execution_enabled:false}:path==='/projects'?[project]:path.endsWith('/workspace')?{project,tasks:[{id:'t',data:task}],files:[],drafts:[],runs:[],messages:[]}:path==='/tasks/t'?{task,draft:null,preview:null}:{status:'disconnected',verified:false};
    await route.fulfill({json:payload});
  });
  await page.goto('/apps/adbuild-agent');await page.waitForLoadState('networkidle');
  await expect(page.getByRole('combobox',{name:'选择搭建任务'})).toHaveValue('t');
}

test('desktop sidebars collapse independently and preserve working state',async({page})=>{
  await page.setViewportSize({width:1440,height:1000});await workspace(page);
  const center=page.locator('.center');
  const initial=(await center.boundingBox())!;
  await page.getByLabel('发送给 Agent 的需求').fill('保留这段未发送的需求');
  await page.getByLabel('搜索广告需求').fill('保留搜索条件');
  const navigation=page.locator('#workspace-navigation');
  await page.getByRole('button',{name:'折叠左侧栏',exact:true}).click();
  await expect(navigation).toBeHidden();
  await expect(page.getByRole('button',{name:'展开左侧栏',exact:true})).toHaveAttribute('aria-expanded','false');
  const leftCollapsed=(await center.boundingBox())!;
  expect(leftCollapsed.width).toBeGreaterThan(initial.width);
  await page.getByRole('button',{name:'折叠右侧栏',exact:true}).click();
  await expect(page.getByLabel('搜索广告需求')).toBeHidden();
  await expect(page.locator('#preview-footer')).toBeHidden();
  const bothCollapsed=(await center.boundingBox())!;
  expect(bothCollapsed.width).toBeGreaterThan(leftCollapsed.width);
  await expect(page.getByLabel('发送给 Agent 的需求')).toHaveValue('保留这段未发送的需求');
  await expect(page.getByRole('combobox',{name:'选择搭建任务'})).toHaveValue('t');
  await page.getByRole('button',{name:'展开右侧栏',exact:true}).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('搜索广告需求')).toHaveValue('保留搜索条件');
  await page.getByRole('button',{name:'展开左侧栏',exact:true}).focus();
  await page.keyboard.press('Space');
  await expect(navigation).toBeVisible();
  expect((await center.boundingBox())!.width).toBe(initial.width);
  const header=page.locator('.preview > header');
  const bounds=await header.boundingBox();
  const toggle=await header.getByRole('button',{name:'折叠右侧栏',exact:true}).boundingBox();
  expect(toggle!.x+toggle!.width).toBeGreaterThan(bounds!.x+bounds!.width-25);
  expect(Math.abs(toggle!.y+toggle!.height/2-bounds!.y-bounds!.height/2)).toBeLessThanOrEqual(1);
  await expect(page.locator('.preview-title button')).toHaveCount(0);
  await header.getByRole('button',{name:'折叠右侧栏',exact:true}).click();
  await expect(header.getByRole('button',{name:'展开右侧栏',exact:true})).toBeVisible();
});

test('mobile disclosure controls and preview shortcut work across breakpoints',async({page})=>{
  await page.setViewportSize({width:390,height:844});await workspace(page);
  for(const width of [320,390,760,768,1024,1100,1440,1920]){
    await page.setViewportSize({width,height:1000});
    await page.getByRole('button',{name:'折叠左侧栏',exact:true}).click();
    await page.getByRole('button',{name:'折叠右侧栏',exact:true}).click();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`collapsed layout at ${width}`).toBeTruthy();
    await page.getByRole('button',{name:'展开左侧栏',exact:true}).click();
    await page.getByRole('button',{name:'展开右侧栏',exact:true}).click();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`expanded layout at ${width}`).toBeTruthy();
  }
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'折叠左侧栏',exact:true}).click();
  await expect(page.getByRole('button',{name:'侧栏交互测试项目',exact:true})).toBeHidden();
  await page.getByRole('button',{name:'展开左侧栏',exact:true}).click();
  await expect(page.getByRole('button',{name:'侧栏交互测试项目',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'折叠右侧栏',exact:true}).click();
  const collapsedHeight=(await page.locator('.preview').boundingBox())!.height;
  await page.getByRole('button',{name:'搭建预览',exact:true}).click();
  await expect(page.getByRole('button',{name:'折叠右侧栏',exact:true})).toBeInViewport();
  await expect(page.getByLabel('搜索广告需求')).toBeVisible();
  expect((await page.locator('.preview').boundingBox())!.height).toBeGreaterThan(collapsedHeight);
  await expect(page.getByRole('button',{name:'折叠右侧栏',exact:true})).toHaveAttribute('aria-expanded','true');
});
