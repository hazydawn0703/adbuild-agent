import {test,expect} from '@playwright/test';
import {HttpAgent} from '@ag-ui/client';
import {runTaskAgent} from '../lib/agent';

const action={request_id:'request-current',task_id:'task-current',action:'message',message:'Keep this input'};
const response={request_id:action.request_id,task_id:action.task_id,project_id:'project-current',answer:'Current result'};
function streamAgent(events:(input:Record<string,unknown>)=>Record<string,unknown>[],initialState:Record<string,unknown>={}){
 return new HttpAgent({url:'http://unit.invalid/agent',initialState,fetch:async(_url,init)=>{
  const input=JSON.parse(String(init.body));
  const encoded=[{type:'RUN_STARTED',runId:input.runId,threadId:input.threadId},...events(input)]
   .map(event=>`data: ${JSON.stringify(event)}\n\n`).join('');
  return new Response(encoded,{headers:{'content-type':'text/event-stream'}});
 }});
}

test('official SDK RunError can resolve while retaining old state; task wrapper rejects it',async()=>{
 const old={request_id:'previous',task_id:'previous-task',project_id:'project-current',answer:'Old result'};
 const legacy=streamAgent(()=>[{type:'RUN_ERROR',message:'Model timeout'}],{response:old});
 await legacy.runAgent({runId:'sdk-demonstration'});
 expect(legacy.state.response).toEqual(old);
 const guarded=streamAgent(()=>[{type:'RUN_ERROR',message:'Model timeout'}],{response:old});
 await expect(runTaskAgent(guarded,'project-current',action)).rejects.toThrow('Model timeout');
 expect(guarded.state.response).toBeUndefined();
});

test('successful finish and current server request ownership are both required',async()=>{
 const agent=streamAgent(input=>{
  expect(input.runId).toBe(action.request_id);
  expect(input.forwardedProps).toMatchObject(action);
  expect(input.state).toEqual({});
  return [{type:'STATE_SNAPSHOT',snapshot:{response}},{type:'RUN_FINISHED',runId:input.runId,threadId:input.threadId}];
 },{response:{answer:'Stale value'}});
 await expect(runTaskAgent(agent,'project-current',action)).resolves.toEqual(response);
});

for(const [field,value] of [['request_id','other-request'],['task_id','other-task'],['project_id','other-project']]){
 test(`late snapshot with different ${field} cannot be accepted`,async()=>{
  const agent=streamAgent(input=>[{type:'STATE_SNAPSHOT',snapshot:{response:{...response,[field]:value}}},{type:'RUN_FINISHED',runId:input.runId,threadId:input.threadId}]);
  await expect(runTaskAgent(agent,'project-current',action)).rejects.toThrow('本次请求不一致');
 });
}

test('snapshot followed by RunError is a failure even when its request ID matches',async()=>{
 const agent=streamAgent(()=>[{type:'STATE_SNAPSHOT',snapshot:{response}},{type:'RUN_ERROR',message:'Write result uncertain'}]);
 await expect(runTaskAgent(agent,'project-current',action)).rejects.toThrow('Write result uncertain');
});

test('snapshot without successful terminal event cannot claim completion',async()=>{
 const agent=streamAgent(()=>[{type:'STATE_SNAPSHOT',snapshot:{response}}]);
 await expect(runTaskAgent(agent,'project-current',action)).rejects.toThrow();
});
