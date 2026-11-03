'use client';
import {useState} from 'react';
import {FolderPlus} from 'lucide-react';
import type {Project} from '../lib/types';

export type NewProject={tenant_id:string;name:string;account_id:string};

export default function CreateProjectForm({projects,currentTenant,busy,onSave,onCancel}:{projects:Project[];currentTenant:string;busy:boolean;onSave:(value:NewProject)=>Promise<void>;onCancel:()=>void}) {
  const spaces=[...new Map(projects.filter(project=>project.role==='admin').map(project=>[project.tenant_id,{id:project.tenant_id,name:project.tenant_name}])).values()];
  const [tenant,setTenant]=useState(()=>spaces.some(space=>space.id===currentTenant)?currentTenant:spaces[0]?.id||'');
  const [name,setName]=useState(''),[account,setAccount]=useState(''),[error,setError]=useState('');
  async function submit(e:React.FormEvent<HTMLFormElement>) {
    e.preventDefault();setError('');
    if(!spaces.some(space=>space.id===tenant)){setError('请选择有管理权限的客户空间');return}
    if(!name.trim()){setError('请输入项目名称');return}
    await onSave({tenant_id:tenant,name:name.trim(),account_id:account.trim()});
  }
  return <form className="policy-form modal-form" onSubmit={submit}><div className="modal-body">
    <div className="policy-intro"><FolderPlus size={20}/><div><strong>创建新的广告项目</strong><p>在所选客户空间中管理本项目的需求、任务和搭建预览。</p></div></div>
    <fieldset className="policy-fields project-form-fields" disabled={busy}>
      <label>所属客户空间<select aria-label="所属客户空间" aria-describedby="project-space-help" required value={tenant} onChange={e=>setTenant(e.target.value)}><option value="" disabled>请选择客户空间</option>{spaces.map(space=><option key={space.id} value={space.id}>{space.name}</option>)}</select><span className="policy-help" id="project-space-help">仅显示你拥有管理员权限的客户空间。</span></label>
      <label>项目名称<input data-modal-autofocus required maxLength={255} value={name} onChange={e=>setName(e.target.value)} placeholder="例如：品牌 TikTok 广告投放"/></label>
      <label>广告账户 ID（选填）<input aria-label="广告账户 ID（选填）" aria-describedby="project-account-help" maxLength={100} value={account} onChange={e=>setAccount(e.target.value)} placeholder="输入广告账户 ID"/><span className="policy-help" id="project-account-help">如暂未确定，可留空创建项目。</span></label>
    </fieldset>
    </div>{error&&<p className="modal-error" role="alert">{error}</p>}
    <footer className="modal-footer"><button type="button" className="outline" disabled={busy} onClick={onCancel}>取消</button><button type="submit" className="primary" disabled={busy||!spaces.length}>{busy?'正在创建…':'创建项目'}</button></footer>
  </form>;
}
