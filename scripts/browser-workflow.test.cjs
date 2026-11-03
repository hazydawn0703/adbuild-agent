const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const {safeTransportCode}=require('./browser-workflow.cjs');

test('transport diagnostics expose only a known code rather than request credentials',()=>{
 const diagnostic=safeTransportCode(new Error('socket hang up ECONNRESET; authorization: Bearer private-unit-credential'));
 assert.equal(diagnostic,'ECONNRESET');
 assert.equal(safeTransportCode(new Error('authorization: Bearer private-unit-credential')),'transport_failure');
});

function fixture(){
 const dir=fs.mkdtempSync(path.resolve('.tmp/browser-output-contract-'));
 const login=path.join(dir,'login.json');
 fs.writeFileSync(login,JSON.stringify({email:'unit@example.invalid',password:'not-a-real-password'}),{flag:'wx'});
 return {dir,run(output){return spawnSync(process.execPath,[path.resolve('scripts/browser-workflow.cjs')],{
  env:{...process.env,PREVIEW_URL:'http://127.0.0.1:1/apps/adbuild-agent',REAL_API_URL:'http://127.0.0.1:1',QA_PROJECT_NAME:'Unit fixture',QA_LOGIN_FILE:login,QA_ARTIFACT_DIR:output},
  encoding:'utf8',timeout:15000,windowsHide:true,
 });}};
}

test('browser verification refuses an existing evidence directory before any API call',()=>{
 const f=fixture();const output=path.join(f.dir,'existing');fs.mkdirSync(output);
 const marker=path.join(output,'preserved.txt');fs.writeFileSync(marker,'original evidence',{flag:'wx'});
 const result=f.run(output);
 assert.equal(result.status,1);assert.match(result.stderr,/EEXIST/);
 assert.equal(fs.readFileSync(marker,'utf8'),'original evidence');
 assert.deepEqual(fs.readdirSync(output),['preserved.txt']);
});

test('a fresh run saves preflight failure without launching a browser or fabricating results',()=>{
 const f=fixture();const output=path.join(f.dir,'fresh');const result=f.run(output);
 assert.equal(result.status,1);
 const report=JSON.parse(fs.readFileSync(path.join(output,'report.json'),'utf8'));
 assert.equal(report.passed,false);assert.equal(report.error.name,'TypeError');
 assert.deepEqual(report.steps,[]);assert.deepEqual(report.api,[]);
 assert.equal(report.api_forwarding,'route.fetch to actual isolated API; no mocked responses');
 const before=fs.readFileSync(path.join(output,'report.json'),'utf8');
 assert.match(f.run(output).stderr,/EEXIST/);
 assert.equal(fs.readFileSync(path.join(output,'report.json'),'utf8'),before);
});
