import {test,expect,Page} from '@playwright/test';

const values={post_id:'00123',post_url:'https://www.tiktok.com/@brand/video/00123',name:'测试广告计划',account_id:'0001234567890123456789',objective:'traffic',identity_id:'identity-1',budget:'9007199254740993.25',budget_mode:'lifetime',currency:'USD',timezone:'UTC',start_at:'2030-01-01T10:00:45Z',end_at:'2030-01-04T10:00:45Z',audience:'核心受众',language:'en',cta:'了解更多',landing_url:'https://example.com/product'};
const policy={version:3,enabled:false,account_ids:Array.from({length:8},(_,i)=>'account-'+i),objectives:['followers','video_views','traffic'],audiences:['核心受众'],identities:['identity-1'],currency:'USD',per_item_limit:'50',batch_limit:'500',cumulative_limit:'1000',valid_until:'2030-01-01T00:00:00Z'};

async function setup(page:Page,{role='admin',reject=false,structured=false}={}) {
  let version=4;
  let current={...values};
  const writes:Record<string,unknown>[]=[];
  const row=()=>({...current,id:'r',business_no:'101',included:true,sources:{},classification:'new',history:[],issues:[],structure:structured?{budget_level:'campaign',groups:[{id:'g',ads:[{id:'ad',kind:'post',post_id:'00123'}]}]}:null});
  const project={id:'p',name:'弹窗标准测试项目',tenant_id:'tenant',tenant_name:'客户空间',role,account_id:'0001234567890123456789',policy,reserved:'0'};
  await page.addInitScript(()=>sessionStorage.setItem('adbuild-token','test-token'));
  await page.route('**/apps/adbuild-agent/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname.split('/api')[1];
    if(request.method()==='POST'&&path==='/drafts/d/edit'){
      const body=request.postDataJSON();writes.push(body);
      if(reject)return route.fulfill({status:409,json:{detail:'草稿版本已变化或已经冻结，请刷新'}});
      version++;current={...current,...body.edits[0].values};return route.fulfill({json:{id:'d',version}});
    }
    const draft={id:'d',project_id:'p',version,status:'draft',requirements:[row()],summary:{}};
    const task={id:'t',name:'本批需求',version:1,draft_id:'d',stage:'preview',questions:[],proposal:null,messages:[]};
    const campaign={id:'c',name:current.name,budget_level:'campaign',planned_total:current.budget,budget:{amount:current.budget,currency:current.currency,mode:current.budget_mode},adgroups:[{id:'g',name:'广告组',audience:current.audience,language:current.language,timezone:current.timezone,budget:null,ads:[{id:'ad',name:'广告',material_kind:'post',post_id:'00123',authorization_present:false}]}]};
    const preview={platform_checks:[],draft_id:'d',version,digest:'digest',rows:[{...row(),campaign,action:'create',campaign_name:current.name,adgroup_name:'广告组',ad_name:'广告',field_states:{}}],counts:{campaigns:1,adgroups:1,ads:1,skipped:0},totals:[],issues:[],can_confirm:false,approval_valid:false,confirmation_history:[],approval:null,delivery_note:'真实搭建暂缓'};
    const payload=path==='/config'?{configured:true,execution_enabled:false}:path==='/projects'?[project]:path.endsWith('/workspace')?{project,tasks:[{id:'t',data:task}],files:[],drafts:[draft],runs:[],messages:[]}:path==='/tasks/t'?{task,draft,preview}:path.endsWith('/preview')?preview:{status:'disconnected',verified:false};
    await route.fulfill({json:payload});
  });
  await page.goto('/apps/adbuild-agent');await page.waitForLoadState('networkidle');
  return writes;
}

test('post corrections preserve string IDs and reject conflicting links before atomic submission',async({page})=>{
  const writes=await setup(page);
  await page.locator('.details').getByRole('button',{name:'编辑',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'编辑草稿配置',exact:true});
  const corrected='0001234567890123456789';
  await dialog.getByLabel('帖子 ID',{exact:true}).fill(corrected);
  await dialog.getByRole('button',{name:'保存配置',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('帖子 ID 与链接中的视频 ID 不一致');
  await expect(dialog.getByLabel('帖子 ID',{exact:true})).toHaveValue(corrected);
  expect(writes).toHaveLength(0);
  await dialog.getByLabel('TikTok 帖子链接',{exact:true}).fill(`https://www.tiktok.com/@brand/video/${corrected}`);
  await dialog.getByRole('button',{name:'保存配置',exact:true}).click();
  await expect(dialog).toBeHidden();
  expect(writes).toEqual([{expected_version:4,edits:[{requirement_id:'r',values:{...values,post_id:corrected,post_url:`https://www.tiktok.com/@brand/video/${corrected}`}}]}]);
});

test('policy header and footer stay fixed while only the green-scrollbar body scrolls',async({page})=>{
  await setup(page);await page.getByRole('button',{name:'项目授权',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'项目预授权',exact:true});
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:844});
    const body=dialog.locator('.modal-body'),header=dialog.locator('.modal-header'),footer=dialog.locator('.modal-footer');
    await body.evaluate(e=>{e.scrollTop=0});
    const firstHeader=(await header.boundingBox())!,firstFooter=(await footer.boundingBox())!;
    expect(await body.evaluate(e=>e.scrollHeight>e.clientHeight)).toBeTruthy();
    await body.evaluate(e=>{e.scrollTop=e.scrollHeight});
    expect((await header.boundingBox())!.y).toBeCloseTo(firstHeader.y,0);
    expect((await footer.boundingBox())!.y).toBeCloseTo(firstFooter.y,0);
    await expect(header).toBeInViewport();await expect(footer).toBeInViewport();
    expect(await dialog.evaluate(e=>e.scrollTop)).toBe(0);
    expect(await body.evaluate(e=>getComputedStyle(e).scrollbarColor)).toContain('rgb(191, 211, 176)');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  }
  for(let i=0;i<8;i++){await page.keyboard.press('Tab');expect(await dialog.evaluate(e=>e.contains(document.activeElement))).toBeTruthy()}
  await page.keyboard.press('Escape');await expect(dialog).toBeHidden();
  await expect(page.getByRole('button',{name:'项目授权',exact:true})).toBeFocused();
});

test('configuration editor preserves IDs, decimal precision, dates and the opened draft version',async({page})=>{
  const writes=await setup(page);
  await page.locator('.details').getByRole('button',{name:'编辑',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'编辑草稿配置',exact:true});
  await expect(dialog.locator('.json-editor')).toHaveCount(0);
  await expect(dialog.getByLabel('开始时间（北京时间）',{exact:true})).toHaveValue('2030-01-01T18:00:45');
  await expect(dialog.getByLabel('预算金额',{exact:true})).toHaveValue(values.budget);
  await dialog.getByRole('button',{name:'保存配置',exact:true}).click();await expect(dialog).toBeHidden();
  expect(writes).toEqual([{expected_version:4,edits:[{requirement_id:'r',values}]}]);
});

test('configuration editor sends selected values and keeps inputs on conflicts',async({page})=>{
  const writes=await setup(page,{reject:true});
  await page.locator('.details').getByRole('button',{name:'编辑',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'编辑草稿配置',exact:true});
  await dialog.getByLabel('广告目标',{exact:true}).selectOption('video_views');
  await dialog.getByLabel('预算类型',{exact:true}).selectOption('daily');
  await dialog.getByLabel('预算金额',{exact:true}).fill('100.0001');
  await dialog.getByLabel('开始时间（北京时间）',{exact:true}).fill('2030-02-02T19:00:45');
  await dialog.getByRole('button',{name:'保存配置',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('草稿版本已变化');
  await expect(dialog.getByLabel('预算金额',{exact:true})).toHaveValue('100.0001');
  expect(writes[0]).toEqual({expected_version:4,edits:[{requirement_id:'r',values:{...values,objective:'video_views',budget_mode:'daily',budget:'100.0001',start_at:'2030-02-02T19:00:45+08:00'}}]});
  await dialog.getByRole('button',{name:'取消',exact:true}).click();expect(writes).toHaveLength(1);
});

test('configuration editor fits mobile and the review editor uses the same fixed layout',async({page})=>{
  const writes=await setup(page);await page.setViewportSize({width:390,height:844});
  await page.locator('.details').getByRole('button',{name:'编辑',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'编辑草稿配置',exact:true});
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});const header=(await dialog.locator('.modal-header').boundingBox())!,footer=(await dialog.locator('.modal-footer').boundingBox())!;
    await dialog.locator('.modal-body').evaluate(e=>{e.scrollTop=e.scrollHeight});
    expect((await dialog.locator('.modal-header').boundingBox())!.y).toBe(header.y);
    expect((await dialog.locator('.modal-footer').boundingBox())!.y).toBe(footer.y);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    await expect(dialog.getByRole('button',{name:'保存配置',exact:true})).toBeInViewport();
  }
  await dialog.getByRole('button',{name:'取消',exact:true}).click();
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'搭建前预览与确认',exact:true}).click();
  await page.getByRole('button',{name:'编辑配置',exact:true}).click();
  const nested=page.getByRole('dialog',{name:'编辑搭建配置',exact:true});
  await expect(nested.locator('.modal-header')).toBeVisible();await expect(nested.locator('.modal-footer')).toBeVisible();
  await expect(nested.getByLabel('预算金额',{exact:true})).toHaveValue(values.budget);
  await page.keyboard.press('Escape');await expect(nested).toBeHidden();expect(writes).toHaveLength(0);
});

test('viewers cannot edit configuration details',async({page})=>{
  await setup(page,{role:'viewer'});
  await expect(page.locator('.details').getByRole('button',{name:'编辑',exact:true})).toBeDisabled();
});

test('campaign editor does not submit representative group or material values',async({page})=>{
  const writes=await setup(page,{structured:true});
  await page.locator('.details').getByRole('button',{name:'编辑',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'编辑草稿配置',exact:true});
  await expect(dialog.getByLabel('开始时间（北京时间）',{exact:true})).toHaveCount(0);
  await expect(dialog.getByLabel('帖子 ID',{exact:true})).toHaveCount(0);
  await dialog.getByRole('button',{name:'保存配置',exact:true}).click();
  expect(writes).toEqual([{expected_version:4,edits:[{requirement_id:'r',values:{name:values.name,account_id:values.account_id,objective:values.objective,budget:values.budget,budget_mode:values.budget_mode,currency:values.currency}}]}]);
});

test('short dialogs keep keyboard focus and fit a shrinking visible viewport',async({page})=>{
  await setup(page);await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'创建项目',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'创建项目',exact:true});
  for(let i=0;i<16;i++){
    await page.keyboard.press(i<8?'Tab':'Shift+Tab');
    expect(await dialog.evaluate(e=>e.contains(document.activeElement))).toBeTruthy();
  }
  await page.evaluate(()=>{
    Object.defineProperty(window.visualViewport!,'height',{configurable:true,get:()=>330});
    Object.defineProperty(window.visualViewport!,'offsetTop',{configurable:true,get:()=>90});
    window.visualViewport!.dispatchEvent(new Event('resize'));
  });
  const bounds=(await dialog.boundingBox())!;
  expect(bounds.y).toBeGreaterThanOrEqual(90);
  expect(bounds.y+bounds.height).toBeLessThanOrEqual(420);
  const footer=(await dialog.locator('.modal-footer').boundingBox())!;
  expect(footer.y+footer.height).toBeLessThanOrEqual(420);
  await page.keyboard.press('Escape');await expect(dialog).toBeHidden();
  await expect(page.getByRole('button',{name:'创建项目',exact:true})).toBeFocused();
  expect(await page.evaluate(()=>document.body.style.overflow)).not.toBe('hidden');
});

test('clearing optional configuration fields submits null rather than fabricated values',async({page})=>{
  const writes=await setup(page);
  await page.locator('.details').getByRole('button',{name:'编辑',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'编辑草稿配置',exact:true});
  await dialog.getByLabel('预算金额',{exact:true}).fill('');
  await dialog.getByLabel('广告目标',{exact:true}).selectOption('');
  await dialog.getByLabel('预算类型',{exact:true}).selectOption('');
  await dialog.getByLabel('开始时间（北京时间）',{exact:true}).fill('');
  await dialog.getByLabel('结束时间（北京时间）',{exact:true}).fill('');
  await dialog.getByRole('button',{name:'保存配置',exact:true}).click();
  await expect(dialog).toBeHidden();
  expect(writes[0]).toEqual({expected_version:4,edits:[{requirement_id:'r',values:{...values,budget:null,objective:null,budget_mode:null,start_at:null,end_at:null}}]});
});
