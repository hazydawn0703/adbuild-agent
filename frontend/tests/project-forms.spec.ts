import {test,expect,Page} from '@playwright/test';
import type {ProjectContext} from '../lib/types';

const defaults={account_id:'000123',business_no:'需求01',name:'默认广告计划',post_url:'https://www.tiktok.com/@brand/video/123',post_id:'001234',objective:'traffic',budget:'9007199254740993.25',budget_mode:'lifetime',currency:'USD',start_at:'2030-01-01T00:00:45Z',end_at:'2030-02-01T12:30:45+08:00',timezone:'Asia/Shanghai',audience:'核心受众',language:'en',identity_id:'000456',cta:'了解更多',landing_url:'https://example.com/product'};
const initial:ProjectContext={version:7,defaults,account_ids:['000123'],objectives:['traffic'],terms:{HQ:'核心受众',NA:'北美区域'},confirmed_by:'original-owner'};
const baseProject={id:'p',name:'默认配置测试项目',tenant_id:'space-a',tenant_name:'客户空间 A',role:'admin',account_id:'000123',policy:null,reserved:'0'};

async function setup(page:Page,{role='admin',context=initial,reject=false,multiSpace=false}={}) {
  let current=context;
  const projects=[{...baseProject,role}];
  if(multiSpace)projects.push({...baseProject,id:'p-a2',name:'同空间另一项目'}, {...baseProject,id:'p-b',name:'空间 B 的项目',tenant_id:'space-b',tenant_name:'客户空间 B'}, {...baseProject,id:'p-c',name:'只读客户项目',tenant_id:'space-c',tenant_name:'客户空间 C',role:'viewer'});
  const writes:{path:string;body:Record<string,unknown>}[]=[];
  await page.addInitScript(multi=>{sessionStorage.setItem('adbuild-token','test-token');sessionStorage.setItem('adbuild-project',multi?'p-b':'p')},multiSpace);
  await page.route('**/apps/adbuild-agent/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname.split('/api')[1];
    if(request.method()==='POST'){
      const body=request.postDataJSON();writes.push({path,body});
      if(path.endsWith('/context')){
        if(reject)return route.fulfill({status:409,json:{detail:'项目上下文已变化'}});
        current={...body,version:body.version+1,confirmed_by:'current-admin'};
        return route.fulfill({json:current});
      }
      if(path==='/projects'){
        const space=projects.find(project=>project.tenant_id===body.tenant_id)!;
        const project={...baseProject,...body,id:'new-project',tenant_name:space.tenant_name};
        projects.push(project);return route.fulfill({json:project});
      }
      throw new Error('Unexpected write: '+path);
    }
    const project=projects.find(item=>path.startsWith(`/projects/${item.id}/`))||projects[0];
    const payload=path==='/config'?{configured:true,execution_enabled:false}:path==='/projects'?projects:path.endsWith('/context')?current:path.endsWith('/workspace')?{project,tasks:[],files:[],drafts:[],runs:[],messages:[]}:{status:'disconnected',verified:false};
    await route.fulfill({json:payload});
  });
  await page.goto('/apps/adbuild-agent');await page.waitForLoadState('networkidle');
  return writes;
}

test('default configuration round-trips every supported field without changing precision or version',async({page})=>{
  const writes=await setup(page);
  await page.getByRole('button',{name:'项目默认配置',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'项目默认配置',exact:true});
  await expect(dialog.locator('.json-editor')).toHaveCount(0);
  await expect(dialog.getByRole('combobox',{name:/默认字段 /})).toHaveCount(17);
  await expect(dialog.getByLabel('默认开始时间',{exact:true})).toHaveValue('2030-01-01T08:00:45');
  await expect(dialog.getByLabel('默认预算金额',{exact:true})).toHaveValue(defaults.budget);
  await dialog.getByRole('button',{name:'保存默认配置',exact:true}).click();
  await expect(dialog).toBeHidden();
  expect(writes).toEqual([{path:'/projects/p/context',body:initial}]);
});

test('defaults and terms can be edited without silent overwrites or widening the chosen scope',async({page})=>{
  const writes=await setup(page,{context:{...initial,defaults:{currency:'USD',budget:'50'},account_ids:[],objectives:[],terms:{}}});
  await page.getByRole('button',{name:'项目默认配置',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'项目默认配置',exact:true});
  await expect(dialog.getByText('留空时适用于本项目的所有广告账户。')).toBeVisible();
  await dialog.getByRole('button',{name:'添加适用广告账户',exact:true}).click();
  await dialog.getByLabel('适用广告账户 1',{exact:true}).fill('  000999  ');
  await dialog.getByRole('checkbox',{name:'视频观看',exact:true}).check();
  await dialog.getByRole('button',{name:'添加默认字段',exact:true}).click();
  await dialog.getByLabel('默认字段 3',{exact:true}).selectOption('budget_mode');
  await dialog.getByLabel('默认预算类型',{exact:true}).selectOption('daily');
  await dialog.getByRole('button',{name:'移除默认币种',exact:true}).click();
  for(let index=1;index<=2;index++){
    await dialog.getByRole('button',{name:'添加术语',exact:true}).click();
    await dialog.getByLabel(`术语 ${index}`,{exact:true}).fill('HQ');
    await dialog.getByLabel(`术语含义 ${index}`,{exact:true}).fill('核心受众');
  }
  await dialog.getByRole('button',{name:'保存默认配置',exact:true}).click();
  await expect(dialog.getByRole('alert')).toHaveText('术语名称不能重复');
  expect(writes).toHaveLength(0);
  await dialog.getByRole('button',{name:'移除术语 2',exact:true}).click();
  await dialog.getByRole('button',{name:'保存默认配置',exact:true}).click();
  await expect(dialog).toBeHidden();
  expect(writes[0].body).toEqual({...initial,account_ids:['000999'],objectives:['video_views'],defaults:{budget:'50',budget_mode:'daily'},terms:{HQ:'核心受众'}});
});

test('version conflicts retain the edited values and cancellation does not submit',async({page})=>{
  const writes=await setup(page,{reject:true});
  await page.getByRole('button',{name:'项目默认配置',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'项目默认配置',exact:true});
  await dialog.getByLabel('默认币种',{exact:true}).fill('EUR');
  await dialog.getByRole('button',{name:'保存默认配置',exact:true}).click();
  await expect(dialog.getByRole('alert')).toHaveText('项目上下文已变化');
  await expect(dialog.getByLabel('默认币种',{exact:true})).toHaveValue('EUR');
  await dialog.getByRole('button',{name:'取消',exact:true}).click();
  expect(writes).toHaveLength(1);
});

test('create project lists only managed spaces and selects the newly created project',async({page})=>{
  const writes=await setup(page,{multiSpace:true});
  await page.getByRole('button',{name:'创建项目',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'创建项目',exact:true});
  await expect(dialog.locator('.json-editor')).toHaveCount(0);
  await expect(dialog.getByLabel('所属客户空间',{exact:true})).toHaveValue('space-b');
  await expect(dialog.getByLabel('所属客户空间',{exact:true}).locator('option')).toHaveCount(3);
  await expect(dialog.getByLabel('所属客户空间',{exact:true}).locator('option[value=space-c]')).toHaveCount(0);
  await dialog.getByLabel('项目名称',{exact:true}).fill('  新建投放项目  ');
  await dialog.getByLabel('广告账户 ID（选填）',{exact:true}).fill('0001234567890123456789');
  await dialog.getByRole('button',{name:'创建项目',exact:true}).click();
  await expect(dialog).toBeHidden();
  expect(writes).toEqual([{path:'/projects',body:{tenant_id:'space-b',name:'新建投放项目',account_id:'0001234567890123456789'}}]);
  await expect(page.locator('.project.selected')).toHaveText('新建投放项目');
  await expect(page.locator('.breadcrumb')).toContainText('新建投放项目');
});

test('forms fit mobile screens; cancelling creation and read-only accounts cannot write',async({page})=>{
  const writes=await setup(page);
  for(const name of ['项目默认配置','创建项目']){
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('button',{name,exact:true}).click();
    const dialog=page.getByRole('dialog',{name,exact:true});
    for(const width of [390,320]){
      await page.setViewportSize({width,height:844});
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
      const bounds=(await dialog.boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x+bounds.width).toBeLessThanOrEqual(width);
      const save=dialog.getByRole('button',{name:name==='创建项目'?'创建项目':'保存默认配置',exact:true});
      await save.scrollIntoViewIfNeeded();await expect(save).toBeInViewport();
    }
    await dialog.getByRole('button',{name:'取消',exact:true}).click();
  }
  expect(writes).toHaveLength(0);
  await page.unroute('**/apps/adbuild-agent/api/**');await setup(page,{role:'viewer'});
  await expect(page.getByRole('button',{name:'项目默认配置',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'创建项目',exact:true})).toBeDisabled();
});
