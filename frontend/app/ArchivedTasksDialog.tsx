'use client';
import {useCallback,useEffect,useState} from 'react';
import {ArchiveRestore,Eye} from 'lucide-react';
import Modal from './Modal';
import {api} from '../lib/api';
import type {Project} from '../lib/types';

type ArchivedTask={id:string;name:string;version:number;archived_at:string};
export default function ArchivedTasksDialog({project,token,onClose,onView,onRestored}:{project:Project;token:string;onClose:()=>void;onView:(id:string)=>Promise<void>;onRestored:()=>Promise<void>}){
 const [tasks,setTasks]=useState<ArchivedTask[]>([]),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[target,setTarget]=useState<ArchivedTask|null>(null);
 const reload=useCallback(async()=>{const w=await api(`/projects/${project.id}/workspace`,token);setTasks(w.tasks.map((t:{data:ArchivedTask})=>t.data).filter((t:ArchivedTask)=>t.archived_at));},[project.id,token]);
 useEffect(()=>{let active=true;api(`/projects/${project.id}/workspace`,token).then(w=>{if(active)setTasks(w.tasks.map((t:{data:ArchivedTask})=>t.data).filter((t:ArchivedTask)=>t.archived_at))}).catch(e=>{if(active)setError(e.message)}).finally(()=>{if(active)setLoading(false)});return()=>{active=false}},[project.id,token]);
 async function view(id:string){setBusy(true);setError('');try{await onView(id);onClose()}catch(e){setError(e instanceof Error?e.message:'读取任务失败')}finally{setBusy(false)}}
 async function restore(){if(!target)return;setBusy(true);setError('');try{await api(`/tasks/${target.id}/archive`,token,{method:'POST',body:JSON.stringify({expected_version:target.version,archived:false})});await reload();await onRestored();setTarget(null)}catch(e){setError(e instanceof Error?e.message:'恢复失败')}finally{setBusy(false)}}
 return <Modal title="已归档搭建任务" className="policy-modal" busy={busy} error={target?'':error} onClose={onClose}>
  <div className="modal-body"><p className="login-description">{project.name}</p>{loading?<p role="status">正在读取已归档任务…</p>:!tasks.length?<p>暂无已归档搭建任务。</p>:<ul className="archived-task-list">{tasks.map(t=><li key={t.id}><div><strong>{t.name}</strong><small>归档于 {new Date(t.archived_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）</small></div><div className="archived-task-actions"><button className="outline" disabled={busy} onClick={()=>view(t.id)}><Eye size={14}/>查看任务</button><button className="outline" disabled={busy||project.role==='viewer'} onClick={()=>{setError('');setTarget(t)}}><ArchiveRestore size={14}/>恢复任务</button></div></li>)}</ul>}</div>
  <footer className="modal-footer"><button className="outline" disabled={busy} onClick={onClose}>关闭</button></footer>
  {target&&<Modal title="恢复搭建任务" className="policy-modal" busy={busy} error={error} onClose={()=>{setTarget(null);setError('')}}><div className="modal-body"><p>确定恢复「{target.name}」？恢复后可继续处理任务。</p></div><footer className="modal-footer"><button className="outline" disabled={busy} onClick={()=>{setTarget(null);setError('')}}>取消</button><button className="primary" disabled={busy} onClick={restore}>确认恢复</button></footer></Modal>}
 </Modal>
}

