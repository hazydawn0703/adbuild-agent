import {test,expect,Page} from '@playwright/test';

const existing={version:4,enabled:false,account_ids:['account-main'],objectives:['followers','video_views','traffic'],audiences:['受众 A'],identities:['identity-main'],currency:'USD',per_item_limit:'50',batch_limit:'500',cumulative_limit:'1000',valid_until:'2028-04-05T08:30:45Z'};
async function setup(page:Page,role='admin',initial:typeof existing|null=existing) {
  let policy=initial;
  const writes:Record<string,unknown>[]=[];
  await page.addInitScript(()=>sessionStorage.setItem('adbuild-token','test-token'));
  await page.route('**/apps/adbuild-agent/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname.split('/api')[1];
    if(request.method()==='PUT'&&path==='/projects/p/policy'){
      const body=request.postDataJSON();writes.push(body);policy=body;
      return route.fulfill({json:body});
    }
    const project={id:'p',name:'授权表单测试项目',tenant_id:'tenant',role,account_id:'account-main',policy,reserved:'12.50'};
    const payload=path==='/config'?{configured:true,execution_enabled:false}:path==='/projects'?[project]:path.endsWith('/workspace')?{project,tasks:[],files:[],drafts:[],runs:[],messages:[]}:{status:'disconnected',verified:false};
    await route.fulfill({json:payload});
  });
  await page.goto('/apps/adbuild-agent');await page.waitForLoadState('networkidle');
  return writes;
}

test('policy form loads current settings and submits exact authorization values',async({page})=>{
  const writes=await setup(page);
  await expect(page.locator('.cloud-state,.workspace-select,.page-heading')).toHaveCount(0);
  await page.locator('.topbar').getByRole('button',{name:'项目授权',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'项目预授权'});
  await expect(dialog.locator('.json-editor')).toHaveCount(0);
  await expect(dialog.getByLabel('授权截止时间（北京时间）')).toHaveValue('2028-04-05T16:30:45');
  await expect(dialog.getByLabel('广告账户 1',{exact:true})).toHaveValue('account-main');
  const currencyBounds=(await dialog.getByLabel('预算币种',{exact:true}).boundingBox())!;
  const budgetBounds=(await dialog.getByLabel('单条预算上限',{exact:true}).boundingBox())!;
  expect(currencyBounds.width).toBeGreaterThan(250);
  expect(Math.abs(currencyBounds.width-budgetBounds.width)).toBeLessThanOrEqual(1);
  await dialog.getByRole('switch',{name:'启用项目预授权'}).check();
  await dialog.getByRole('button',{name:'添加广告账户',exact:true}).click();
  await dialog.getByLabel('广告账户 2',{exact:true}).fill('  account-other  ');
  await dialog.getByRole('checkbox',{name:'视频观看',exact:true}).uncheck();
  await dialog.getByLabel('预算币种',{exact:true}).fill('eur');
  await dialog.getByLabel('单条预算上限',{exact:true}).fill('9007199254740993.25');
  await dialog.getByLabel('单批预算上限',{exact:true}).fill('9007199254740994.25');
  await dialog.getByLabel('累计预算上限',{exact:true}).fill('9007199254740995.25');
  await dialog.getByRole('button',{name:'保存预授权',exact:true}).click();
  await expect(dialog).toBeHidden();
  expect(writes).toEqual([{...existing,version:5,enabled:true,account_ids:['account-main','account-other'],objectives:['followers','traffic'],currency:'EUR',per_item_limit:'9007199254740993.25',batch_limit:'9007199254740994.25',cumulative_limit:'9007199254740995.25',valid_until:'2028-04-05T16:30:45+08:00'}]);
});

test('enabled authorization cannot silently allow empty lists; cancel never writes',async({page})=>{
  const writes=await setup(page,'admin',null);
  await page.getByRole('button',{name:'项目授权',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'项目预授权'});
  await dialog.getByLabel('授权截止时间（北京时间）').fill('2028-04-05T16:30:45');
  await dialog.getByRole('switch',{name:'启用项目预授权'}).check();
  await dialog.getByRole('button',{name:'保存预授权',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('启用前请至少添加');
  expect(writes).toHaveLength(0);
  await dialog.getByRole('button',{name:'添加受众模板',exact:true}).click();
  await dialog.getByLabel('受众模板 1',{exact:true}).fill('受众 A');
  await dialog.getByRole('button',{name:'添加广告身份',exact:true}).click();
  await dialog.getByLabel('广告身份 1',{exact:true}).fill('identity-main');
  await dialog.getByLabel('单条预算上限',{exact:true}).fill('0');
  await dialog.getByRole('button',{name:'保存预授权',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('大于 0');
  expect(writes).toHaveLength(0);
  await dialog.getByRole('button',{name:'取消',exact:true}).click();
  await expect(dialog).toBeHidden();
  expect(writes).toHaveLength(0);
});

test('policy form fits mobile screens and non-admins cannot open it',async({page})=>{
  await setup(page);
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'项目授权',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'项目预授权'});
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    const bounds=(await dialog.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x+bounds.width).toBeLessThanOrEqual(width);
    await dialog.getByRole('button',{name:'保存预授权',exact:true}).scrollIntoViewIfNeeded();
    await expect(dialog.getByRole('button',{name:'保存预授权',exact:true})).toBeInViewport();
  }
  await dialog.getByRole('button',{name:'取消',exact:true}).click();
  await page.unroute('**/apps/adbuild-agent/api/**');
  await setup(page,'viewer');
  await expect(page.locator('.topbar').getByRole('button',{name:'项目授权',exact:true})).toBeDisabled();
});
