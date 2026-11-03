import {test,expect} from '@playwright/test';

test('a pending structure proposal invalidates a prior approval without changing canonical draft values',async({page})=>{
 const errors:string[]=[];const forbidden:string[]=[];let proposed:Record<string,unknown>|null=null;
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.method()==='POST'&&/\/(execute|confirm)$/.test(new URL(r.url()).pathname))forbidden.push(r.url())});
 const row={id:'row',business_no:'ref-17',included:true,name:'New campaign',account_id:'7800000000000000011',post_id:'7800000000000000022',post_url:'',objective:'video_views',budget:'101.01',budget_mode:'lifetime',currency:'USD',start_at:'2027-02-01T09:00:00Z',end_at:'2027-02-05T09:00:00Z',timezone:'UTC',audience:'Confirmed audience',language:'en',identity_id:'7800000000000000033',cta:'',landing_url:'',sources:{},classification:'new',history:[],issues:[],structure:null};
 const campaign={id:'campaign',name:'New campaign',budget_level:'campaign',planned_total:'101.01',budget:{amount:'101.01',currency:'USD',mode:'lifetime'},adgroups:[{id:'group',name:'Default group',audience:row.audience,language:'en',timezone:'UTC',budget:null,ads:[{id:'ad',name:'Ad',material_kind:'post',post_id:row.post_id,authorization_present:false}]}]};
 const draft={id:'draft',project_id:'project',version:4,status:'approved',requirements:[row],summary:{}};
 const task:{id:string;name:string;version:number;draft_id:string;questions:unknown[];messages:unknown[];proposal:unknown}={id:'task',name:'Structure review',version:6,draft_id:draft.id,questions:[],messages:[],proposal:null};
 const project={id:'project',name:'Test project',tenant_id:'tenant',role:'admin',account_id:row.account_id,policy:null,reserved:'0'};
 const preview={draft_id:draft.id,version:draft.version,digest:'current-digest',rows:[{...row,campaign,campaign_name:row.name,action:'create'}],counts:{campaigns:1,adgroups:1,ads:1,skipped:0},totals:[{currency:'USD',budget_mode:'lifetime',amount:'101.01'}],issues:[] as {requirement_id:string;code:string;message:string}[],can_confirm:true,approval_valid:true,approval:{approved_at:'2026-11-01T09:00:00Z',approved_by:'reviewer'} as {approved_at:string;approved_by:string}|null,confirmation_history:[],platform_checks:[{item:'账户能力',status:'unverified'},{item:'Spark 素材权限',status:'unverified'}],delivery_note:'真实搭建暂缓'};
 await page.addInitScript(()=>sessionStorage.setItem('adbuild-token','test-token'));
 await page.route('**/apps/adbuild-agent/api/**',async route=>{
  const path=new URL(route.request().url()).pathname.split('/api')[1];
  if(path.endsWith('/structure-proposal')){proposed=route.request().postDataJSON();task.proposal={id:'proposal',draft_version:4,status:'pending',reason:'分为两个广告组',edits:[{requirement_id:'row',values:{structure:(proposed as Record<string,unknown>).structure}}]};preview.can_confirm=false;preview.approval_valid=false;preview.approval=null;preview.digest='pending-proposal-digest';preview.issues=[{requirement_id:'row',code:'semantic_review',message:'存在待采纳的修改提案，请先采纳或拒绝，再确认完整方案'}];}
  const payload=path==='/config'?{configured:true,execution_enabled:false}:path==='/projects'?[project]:path.endsWith('/workspace')?{project,tasks:[{id:task.id,data:task}],files:[],drafts:[draft],runs:[],messages:[]}:path===`/tasks/${task.id}`?{task,draft,preview}:path.endsWith('/preview')?preview:path.endsWith('/structure-proposal')?{task}:{status:'disconnected',verified:false};
  await route.fulfill({json:payload});
 });
 await page.goto('/apps/adbuild-agent');await page.waitForLoadState('networkidle');
 await page.getByRole('button',{name:'搭建前预览与确认',exact:true}).click();
 await expect(page.getByText('账户能力：未核实')).toBeVisible();
 await expect(page.getByRole('button',{name:'已确认此版本',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'调整分组与素材',exact:true}).click();
 await page.getByLabel('结构预算层级').selectOption('adgroup');
 await page.getByRole('button',{name:'添加广告组',exact:true}).click();
 const editor=page.getByRole('dialog',{name:'调整广告组与素材结构',exact:true});
 for(const size of [{width:390,height:844},{width:320,height:568},{width:667,height:375},{width:390,height:330}]){
  await page.setViewportSize(size);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const header=(await editor.locator('.modal-header').boundingBox())!,footer=(await editor.locator('.modal-footer').boundingBox())!;
  await editor.locator('.modal-body').evaluate(e=>e.scrollTop=e.scrollHeight);
  expect((await editor.locator('.modal-header').boundingBox())!.y).toBe(header.y);
  expect((await editor.locator('.modal-footer').boundingBox())!.y).toBe(footer.y);
  expect(footer.y+footer.height).toBeLessThanOrEqual(size.height);
  expect(await editor.locator('.modal-body').evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBeTruthy();
 }
 await page.setViewportSize({width:1440,height:900});
 await page.getByLabel('组预算金额',{exact:true}).nth(0).fill('50');
 await page.getByLabel('组预算金额',{exact:true}).nth(1).fill('51.01');
 await page.getByLabel('受众',{exact:true}).nth(0).fill('Audience north');
 await page.getByLabel('受众',{exact:true}).nth(1).fill('Audience south');
 await page.getByRole('button',{name:'生成结构修改提案',exact:true}).click();
 await expect(page.getByRole('heading',{name:'调整广告组与素材结构'})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'确认搭建方案',exact:true})).toBeDisabled();
 await expect(page.getByRole('button',{name:'已确认此版本',exact:true})).toHaveCount(0);
 await expect(page.getByText('存在待采纳的修改提案，请先采纳或拒绝，再确认完整方案',{exact:true})).toBeVisible();
 expect(draft.version).toBe(4);expect(draft.requirements[0].structure).toBeNull();
 expect(proposed).toMatchObject({expected_version:4,requirement_id:'row',structure:{budget_level:'adgroup',groups:[{audience:'Audience north',budget:{amount:'50'}},{audience:'Audience south',budget:{amount:'51.01'}}]}});
 await page.getByRole('button',{name:'关闭搭建预览',exact:true}).click();
 await expect(page.getByText('分为两个广告组',{exact:true})).toBeVisible();
 expect(errors).toEqual([]);expect(forbidden).toEqual([]);
});
