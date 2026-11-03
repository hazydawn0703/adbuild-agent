import {test,expect} from '@playwright/test';

test('task selection and questions survive refresh without execution',async({page})=>{
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
 await page.setViewportSize({width:1280,height:900});
 await page.getByLabel('退出登录').click();
 await expect(page.getByText('第二批待补充')).toHaveCount(0);
 await expect(page.getByLabel('本批使用哪种币种？')).toHaveCount(0);
 expect(errors).toEqual([]);expect(writes).toEqual([]);
});

test('pending proposal restores and unresolved conflict disables final confirmation',async({page})=>{
 const row={id:'r',business_no:'101',included:true,name:'测试计划',account_id:'account',post_id:'123',post_url:'https://www.tiktok.com/@a/video/123',objective:'followers',budget:'50',budget_mode:'lifetime',currency:'USD',start_at:'2027-01-01T10:00:00Z',end_at:'2027-01-04T10:00:00Z',timezone:'UTC',audience:'HQ',language:'en',identity_id:'identity',cta:'',landing_url:'',sources:{budget:{file_id:'f',sheet:'Sheet',cell:'B2',raw_value:'50',kind:'excel'}},classification:'new',history:[],issues:[]};
 const draft={id:'d',project_id:'p',version:1,status:'draft',requirements:[row],summary:{}};
 const task={id:'t',name:'待采纳批次',version:2,draft_id:'d',questions:[],messages:[],proposal:{id:'proposal',draft_version:1,status:'pending',reason:'仅修改本条预算',edits:[{requirement_id:'r',values:{budget:'40'}}]}};
 const project={id:'p',name:'项目',tenant_id:'tenant',role:'admin',account_id:'account',policy:null,reserved:'0'};
 const campaign={id:'campaign',name:'测试计划',budget_level:'campaign',planned_total:'50',budget:{amount:'50',currency:'USD',mode:'lifetime'},adgroups:[{id:'group',name:'广告组',audience:'HQ',language:'en',timezone:'UTC',budget:null,ads:[{id:'ad',name:'广告',material_kind:'post',post_id:'123',authorization_present:false}]}]};
 const preview={draft_id:'d',version:1,digest:'digest',rows:[{...row,action:'create',campaign_name:'测试计划',campaign,field_states:{currency:'conflict'}}],counts:{campaigns:1,adgroups:1,ads:1,skipped:0},totals:[],issues:[{requirement_id:'r',message:'币种存在冲突'}],can_confirm:false,approval_valid:false,approval:null,confirmation_history:[],platform_checks:[{item:'账户能力',status:'unverified'}]};
 await page.addInitScript(()=>sessionStorage.setItem('adbuild-token','test-token'));
 await page.route('**/apps/adbuild-agent/api/**',async route=>{
  const path=new URL(route.request().url()).pathname.split('/api')[1];
  const payload=path==='/config'?{configured:true,execution_enabled:false}:path==='/projects'?[project]:path.endsWith('/workspace')?{project,tasks:[{id:'t',data:task}],files:[],drafts:[draft],runs:[],messages:[]}:path==='/tasks/t'?{task,draft,preview}:path.endsWith('/preview')?preview:{status:'disconnected',verified:false};
  await route.fulfill({json:payload});
 });
 await page.goto('/apps/adbuild-agent');await page.waitForLoadState('networkidle');
 await expect(page.getByRole('button',{name:'确认修改',exact:true})).toBeVisible();
 await page.reload();await page.waitForLoadState('networkidle');
 await expect(page.getByRole('button',{name:'确认修改',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'搭建前预览与确认',exact:true}).click();
 await expect(page.getByRole('button',{name:'确认搭建方案',exact:true})).toBeDisabled();
 await expect(page.getByText('暂不能确认搭建', {exact:true})).toBeVisible();
});
