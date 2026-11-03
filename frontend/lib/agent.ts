import type {HttpAgent} from '@ag-ui/client';

type TaskAction={request_id:string;task_id:string;[key:string]:unknown};
type TaskResponse={request_id:string;task_id:string;project_id:string;answer:string};

export async function runTaskAgent(agent:HttpAgent,projectId:string,action:TaskAction):Promise<TaskResponse>{
 let runError:string|undefined;
 let completed=false;
 // Server state is authoritative. An earlier snapshot must not satisfy this run.
 agent.setState({});
 await agent.runAgent({runId:action.request_id,forwardedProps:action},{
  onRunErrorEvent:({event})=>{runError=event.message||'Agent 分析失败';},
  onRunFailed:({error})=>{runError=error.message||'Agent 分析中断';},
  onRunFinishedEvent:({event,outcome})=>{completed=event.runId===action.request_id&&outcome==='success';},
 });
 if(runError)throw new Error(runError);
 if(!completed)throw new Error('Agent 分析未正常完成，已保留输入，请刷新任务后恢复');
 const response=agent.state.response;
 if(!response||typeof response!=='object')throw new Error('Agent 未返回本次请求的有效结果');
 const result=response as Record<string,unknown>;
 if(result.request_id!==action.request_id||result.task_id!==action.task_id||result.project_id!==projectId||typeof result.answer!=='string'){
  throw new Error('Agent 返回结果与本次请求不一致，已保留输入，请刷新任务后核对');
 }
 return result as TaskResponse;
}
