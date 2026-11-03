/* Real API browser verification. Routes forward bytes to the isolated QA API; no responses are fabricated. */
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium,expect}=require('../frontend/node_modules/@playwright/test');

async function main(){
 const previewUrl=process.env.PREVIEW_URL;
 const apiUrl=process.env.REAL_API_URL;
 const loginFile=process.env.QA_LOGIN_FILE;
 const projectName=process.env.QA_PROJECT_NAME;
 if(!previewUrl||!apiUrl||!loginFile||!projectName)throw new Error('Required: PREVIEW_URL, REAL_API_URL, QA_LOGIN_FILE, QA_PROJECT_NAME');
 const login=JSON.parse(await fs.readFile(loginFile,'utf8'));
 if(!login.email||!login.password)throw new Error('QA_LOGIN_FILE must contain email and password');
 const output=path.resolve(process.env.QA_ARTIFACT_DIR||`artifacts/real-browser-${new Date().toISOString().replace(/[:.]/g,'-')}`);
 await fs.mkdir(output,{recursive:true});
 const report={started_at:new Date().toISOString(),preview_url:previewUrl,api_url:apiUrl,project:projectName,
  task:process.env.QA_TASK_NAME||null,api_forwarding:'route.fetch to actual isolated API; no mocked responses',
  steps:[],api:[],workspace_snapshots:[],page_errors:[],forbidden_attempts:[],passed:false};
 const configuration=await fetch(apiUrl.replace(/\/$/,'')+'/config').then(async response=>{
  assert(response.ok,'Isolated QA API config is unavailable');return response.json();
 });
 assert.equal(configuration.execution_enabled,false,'Real advertising must stay disabled before browser verification');
 report.execution_enabled=configuration.execution_enabled;
 const browser=await chromium.launch({headless:true,channel:process.env.QA_BROWSER_CHANNEL||'chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.setDefaultTimeout(30000);
 page.on('pageerror',error=>report.page_errors.push(error.message));
 const save=async name=>{await page.screenshot({path:path.join(output,`${name}.png`),fullPage:true});report.steps.push({step:name,at:new Date().toISOString()})};
 await page.route('**/apps/adbuild-agent/api/**',async route=>{
  const request=route.request();const url=new URL(request.url());
  const suffix=url.pathname.split('/apps/adbuild-agent/api')[1];
  if(request.method()==='POST'&&(/\/execute$/.test(suffix)||/^\/runs\//.test(suffix))){
   report.forbidden_attempts.push({method:request.method(),path:suffix});
   await route.abort('blockedbyclient');return;
  }
  const response=await route.fetch({url:apiUrl.replace(/\/$/,'')+suffix+url.search,timeout:240000});
  report.api.push({method:request.method(),path:suffix,status:response.status()});
  if(suffix.endsWith('/workspace')&&response.ok()){
   const payload=await response.json();
   report.workspace_snapshots.push({project_id:payload.project.id,project_name:payload.project.name,runs:payload.runs.length,reserved:String(payload.project.reserved)});
  }
  await route.fulfill({response});
 });
 try{
  await page.goto(previewUrl);await page.waitForLoadState('networkidle');
  await page.getByRole('button',{name:/登录工作空间/}).click();
  await page.getByLabel('邮箱',{exact:true}).fill(login.email);
  await page.getByLabel('密码',{exact:true}).fill(login.password);
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await expect(page.getByLabel('发送给 Agent 的需求')).toBeEnabled({timeout:60000});
  await page.getByRole('button',{name:projectName,exact:true}).click();
  await expect(page.locator('h1')).toHaveText(projectName);
  await page.waitForLoadState('networkidle');
  if(process.env.QA_INPUT_DIR){
   const input=path.resolve(process.env.QA_INPUT_DIR);
   await page.getByRole('button',{name:'＋ 新建搭建任务',exact:true}).click();
   await page.waitForLoadState('networkidle');
   const message=await fs.readFile(path.join(input,'message.txt'),'utf8');
   await page.getByLabel('发送给 Agent 的需求').fill(message);
   await page.getByRole('button',{name:'发送消息',exact:true}).click();
   await expect(page.getByLabel('发送给 Agent 的需求')).toHaveValue('',{timeout:240000});
   const accept=page.getByRole('button',{name:'确认修改',exact:true});
   if(await accept.isVisible()){
    await accept.click();await expect(accept).toHaveCount(0,{timeout:60000});
    report.steps.push({step:'accepted_unchanged_demand_proposal',at:new Date().toISOString()});
   }
   // Upload original files through the product UI. Normal mapping review is explicit below.
   for(const [kind,file] of [['ExportAds 记录','export.xlsx'],['需求 Excel','brief.xlsx']]){
    await page.locator('label.upload-card').filter({hasText:kind}).locator('input[type=file]').setInputFiles(path.join(input,file));
    await expect(page.locator('.composer-bottom')).not.toContainText('上传文件',{timeout:60000});
    await page.waitForLoadState('networkidle');
   }
   await page.locator('.file-list').getByRole('button',{name:'确认字段'}).last().click();
   await expect(page.locator('.composer-bottom')).not.toContainText('识别字段',{timeout:240000});
   const mapping=page.getByRole('heading',{name:'确认字段映射',exact:true});
   if(await mapping.isVisible()){
    await page.getByRole('button',{name:'确认保存',exact:true}).click();
    await expect(mapping).toHaveCount(0,{timeout:60000});
    report.steps.push({step:'confirmed_unchanged_mapping',at:new Date().toISOString()});
   }
   await page.waitForLoadState('networkidle');
  }else if(process.env.QA_TASK_NAME){
   await page.getByLabel('选择搭建任务').selectOption({label:process.env.QA_TASK_NAME});
   await page.waitForLoadState('networkidle');
  }
  await expect(page.getByRole('button',{name:'搭建前预览与确认',exact:true})).toBeEnabled({timeout:60000});
  const selection=await page.getByLabel('选择搭建任务').inputValue();
  assert(selection,'A persisted QA task must be selected');report.task_id=selection;
  await save('01-task-before-refresh');
  await page.reload();await page.waitForLoadState('networkidle');
  await expect(page.getByLabel('选择搭建任务')).toHaveValue(selection);
  await save('02-task-restored');
  await page.getByRole('button',{name:'搭建前预览与确认',exact:true}).click();
  await expect(page.getByRole('heading',{name:/搭建前预览与确认/})).toBeVisible();
  await expect(page.getByText('账户能力：未核实',{exact:true})).toBeVisible();
  await save('03-real-preview');
  if(process.env.QA_STRUCTURE==='true'){
   await page.getByRole('button',{name:'调整分组与素材',exact:true}).first().click();
   await page.getByLabel('结构预算层级').selectOption('adgroup');
   await page.getByRole('button',{name:'添加广告组',exact:true}).click();
   const modes=page.getByLabel('分配方式',{exact:true});
   await modes.nth(0).selectOption('ratio');await modes.nth(1).selectOption('ratio');
   const ratios=page.getByLabel('比例（0.4 代表 40%）',{exact:true});
   await ratios.nth(0).fill('0.4');await ratios.nth(1).fill('0.6');
   await page.getByRole('button',{name:'生成结构修改提案',exact:true}).click();
   await expect(page.getByRole('heading',{name:'调整广告组与素材结构'})).toHaveCount(0);
   await expect(page.getByRole('button',{name:'确认搭建方案',exact:true})).toBeDisabled();
   await page.getByRole('button',{name:'关闭搭建预览',exact:true}).click();
   await expect(page.getByRole('button',{name:'确认修改',exact:true})).toBeVisible();
   await save('04-structure-proposal-pending');
   await page.getByRole('button',{name:'确认修改',exact:true}).click();
   await expect(page.getByRole('button',{name:'确认修改',exact:true})).toHaveCount(0,{timeout:60000});
   await page.getByRole('button',{name:'搭建前预览与确认',exact:true}).click();
   await expect(page.locator('.review-summary')).toContainText('2 广告组');
   report.structure_accepted=true;
  }
  const confirmed=page.getByRole('button',{name:'已确认此版本',exact:true});
  if(await confirmed.isVisible()){
   assert(process.env.QA_EXPECT_NEW_CONFIRMATION!=='true','Task was already approved; fresh confirmation required');
   report.confirmation_action='preexisting_valid';
  }else{
   await expect(page.getByText('暂不能确认搭建',{exact:true})).toHaveCount(0);
   await page.getByLabel('我已核对所选广告的账户、预算、排期、定向和素材，确认此版本的搭建方案。',{exact:true}).check();
   await page.getByRole('button',{name:'确认搭建方案',exact:true}).click();
   await expect(confirmed).toBeVisible({timeout:60000});
   report.confirmation_action='confirmed_current_version_via_UI';
  }
  await save('05-approved-current-version');
  await page.reload();await page.waitForLoadState('networkidle');
  await expect(page.getByLabel('选择搭建任务')).toHaveValue(selection);
  await page.getByRole('button',{name:'搭建前预览与确认',exact:true}).click();
  await expect(page.getByRole('button',{name:'已确认此版本',exact:true})).toBeVisible();
  await save('06-approval-restored');
  await page.setViewportSize({width:390,height:844});
  await save('07-small-screen-preview');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Small screen overflows');
  assert.deepEqual(report.page_errors,[],'Browser page errors');
  assert.deepEqual(report.forbidden_attempts,[],'Forbidden advertising execution attempted');
  const observed=report.workspace_snapshots.filter(snapshot=>snapshot.project_name===projectName);
  assert(observed.length>0,'No actual QA workspace was observed');
  for(const snapshot of observed){assert.equal(snapshot.runs,observed[0].runs);assert.equal(snapshot.reserved,observed[0].reserved);}
  assert(report.api.some(r=>r.path==='/auth/login'&&r.status===200),'Real login did not complete');
  assert(report.api.some(r=>r.path.endsWith('/preview')&&r.status===200),'Real preview was not read');
  if(report.confirmation_action==='confirmed_current_version_via_UI')assert(report.api.some(r=>r.method==='POST'&&r.path.endsWith('/confirm')&&r.status===200));
  report.passed=true;
 }catch(error){
  report.error={name:error.name,message:error.message};
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});
  throw error;
 }finally{
  report.completed_at=new Date().toISOString();
  await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));
  await browser.close();
 }
 console.log(JSON.stringify({passed:report.passed,task_id:report.task_id,confirmation_action:report.confirmation_action,api_requests:report.api.length,artifact_directory:output}));
}
main().catch(error=>{console.error(`${error.name}: ${error.message}`);process.exitCode=1});
