'use client';
import {useCallback,useEffect,useState} from 'react';
import {Building2,Plus,Search,Users,LogOut,KeyRound,UserPlus,Pencil,Trash2} from 'lucide-react';
import BrandMark from '../BrandMark';
import Modal from '../Modal';
import {opsRequest} from './client';

type Admin={id:string;email:string;active:boolean};
type Tenant={id:string;name:string;project_count:number;admins:Admin[]};
type Dialog={kind:'tenant'|'admin'|'password'|'status'|'rename'|'delete';tenant?:Tenant;admin?:Admin};

export default function OpsConsole() {
  const [tenants,setTenants]=useState<Tenant[]>([]),[loading,setLoading]=useState(true);
  const [query,setQuery]=useState(''),[notice,setNotice]=useState(''),[error,setError]=useState('');
  const [busy,setBusy]=useState(false),[dialog,setDialog]=useState<Dialog|null>(null);
  const refresh=useCallback(async()=>{try{setTenants(await opsRequest('/tenants'));}catch(e){setError((e as Error).message)}finally{setLoading(false)}},[]);
  useEffect(()=>{void refresh()},[refresh]);
  async function changeStatus(tenant:Tenant,admin:Admin) {
    if(admin.active&&tenant.admins.filter(a=>a.active).length===1){setError('请先添加其他启用的管理员，不能停用最后一名管理员。如需移除整个空间，请使用“关闭空间”。');return}
    setBusy(true);setError('');setNotice('');
    try{await opsRequest(`/tenants/${tenant.id}/admins/${admin.id}/status`,'POST',{active:!admin.active});setNotice('管理员状态已更新');setDialog(null);await refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function submit(e:React.FormEvent<HTMLFormElement>) {
    e.preventDefault();if(!dialog)return;
    if(dialog.kind==='status'){await changeStatus(dialog.tenant!,dialog.admin!);return}
    const values=Object.fromEntries(new FormData(e.currentTarget).entries());
    if(dialog.kind==='delete'&&values.confirm_name!==dialog.tenant!.name){setError('请输入完整的客户空间名称以确认删除');return}
    if(values.password!==values.confirm){setError('两次输入的密码不一致');return}
    setBusy(true);setError('');setNotice('');
    try {
      if(dialog.kind==='delete')await opsRequest(`/tenants/${dialog.tenant!.id}/close`,'POST');
      if(dialog.kind==='rename')await opsRequest(`/tenants/${dialog.tenant!.id}/name`,'POST',{name:values.name});
      if(dialog.kind==='tenant')await opsRequest('/tenants','POST',{name:values.name,project_name:values.project_name,account_id:values.account_id,email:values.email,password:values.password});
      if(dialog.kind==='admin')await opsRequest(`/tenants/${dialog.tenant!.id}/admins`,'POST',{email:values.email,password:values.password});
      if(dialog.kind==='password')await opsRequest(`/tenants/${dialog.tenant!.id}/admins/${dialog.admin!.id}/password`,'POST',{password:values.password});
      setNotice(dialog.kind==='delete'?'客户空间及其数据已永久删除':dialog.kind==='rename'?'客户空间名称已更新':dialog.kind==='tenant'?'客户空间已开通，管理员可使用邮箱和初始密码登录工作台':dialog.kind==='admin'?'管理员已添加':'密码已重置，该账号的已有登录会话已失效');
      setDialog(null);await refresh();
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  const filtered=tenants.filter(t=>`${t.name} ${t.admins.map(a=>a.email).join(' ')}`.toLowerCase().includes(query.toLowerCase()));
  return <><header className="ops-header"><a className="ops-brand" href="/apps/adbuild-agent"><BrandMark size={34}/>adbuild<span>平台管理</span></a><div className="ops-header-actions"><span className="ops-role">平台管理员</span><a href="/apps/adbuild-agent">客户工作台 ↗</a><button disabled={busy} onClick={async()=>{setBusy(true);try{await opsRequest('/logout','POST');window.location.replace('/apps/adbuild-agent/ops/login')}catch(e){setError((e as Error).message);setBusy(false)}}}><LogOut size={15}/>退出管理</button></div></header>
    <main className="ops-main"><div className="ops-title"><div><p className="ops-eyebrow">PLATFORM OPERATIONS</p><h1>客户空间管理</h1><p className="ops-muted">开通客户空间，维护客户管理员的访问权限。</p></div><button className="ops-primary" disabled={busy} onClick={()=>{setError('');setDialog({kind:'tenant'})}}><Plus size={17}/>开通客户空间</button></div>
      <div className="ops-stats"><section><Building2 size={20}/><div><strong>{tenants.length}</strong><span>客户空间</span></div></section><section><Users size={20}/><div><strong>{tenants.reduce((n,t)=>n+t.admins.filter(a=>a.active).length,0)}</strong><span>启用的管理员</span></div></section><section><KeyRound size={20}/><div><strong>独立验证</strong><span>平台权限与客户权限隔离</span></div></section></div>
      {notice&&<p className="ops-notice" role="status">{notice}</p>}{error&&!dialog&&<p className="ops-error" role="alert">{error}<button onClick={()=>{setError('');void refresh()}}>重新加载</button></p>}
      <div className="ops-list-heading"><h2>全部客户空间</h2><label className="ops-search"><Search size={16}/><input aria-label="搜索客户空间或管理员邮箱" placeholder="搜索客户空间或管理员邮箱" value={query} onChange={e=>setQuery(e.target.value)}/></label></div>
      {loading?<p className="ops-empty">正在加载客户空间…</p>:filtered.length===0?<div className="ops-empty"><Building2 size={28}/><h3>{query?'没有匹配的客户空间':'还没有客户空间'}</h3><p>{query?'试试其他名称或邮箱':'点击“开通客户空间”，创建客户的第一个项目和管理员账号。'}</p></div>:<div className="ops-tenants">{filtered.map(tenant=><section className="ops-tenant" key={tenant.id}><div className="ops-tenant-heading"><div className="ops-tenant-name"><span><Building2 size={20}/></span><div><h2 className="ops-name-title">{tenant.name}<button className="ops-rename" aria-label={`修改客户空间「${tenant.name}」的名称`} disabled={busy} onClick={()=>{setError('');setDialog({kind:'rename',tenant})}}><Pencil size={15}/></button></h2><p>{tenant.project_count} 个项目 · {tenant.admins.length} 名管理员</p></div></div><div className="ops-tenant-actions"><button className="ops-secondary" disabled={busy} onClick={()=>{setError('');setDialog({kind:'admin',tenant})}}><UserPlus size={15}/>添加管理员</button><button className="ops-secondary ops-danger" disabled={busy} onClick={()=>{setError('');setDialog({kind:'delete',tenant})}}><Trash2 size={15}/>关闭空间</button></div></div>
        <div className="ops-table-wrap"><table><thead><tr><th>管理员邮箱</th><th>角色</th><th>账号状态</th><th>操作</th></tr></thead><tbody>{tenant.admins.map(admin=><tr key={admin.id}><td>{admin.email}</td><td>客户管理员</td><td><span className={`ops-status ${admin.active?'':'ops-inactive'}`}>{admin.active?'已启用':'已停用'}</span></td><td><div className="ops-row-actions"><button disabled={busy} onClick={()=>{setError('');setDialog({kind:'password',tenant,admin})}}>重置密码</button><button disabled={busy} onClick={()=>{setError('');setDialog({kind:'status',tenant,admin})}}>{admin.active?'停用账号':'启用账号'}</button></div></td></tr>)}</tbody></table></div>
      </section>)}</div>}
      <p className="ops-footnote">平台管理员负责开通与账号管理；客户管理员仅管理所属空间中的项目。每个空间至少保留一名启用的管理员。</p>
    </main>
    {dialog&&<Modal title={dialog.kind==='delete'?'永久关闭客户空间':dialog.kind==='rename'?'修改客户空间名称':dialog.kind==='tenant'?'开通客户空间':dialog.kind==='admin'?'添加客户管理员':dialog.kind==='password'?'重置管理员密码':dialog.admin!.active?'停用管理员账号':'启用管理员账号'} closeLabel="关闭弹窗" className="ops-dialog" busy={busy} error={error} onClose={()=>{setDialog(null);setError('')}}>
      <form className="modal-form" onSubmit={submit} key={`${dialog.kind}-${dialog.admin?.id||dialog.tenant?.id||''}`}>
        <div className="modal-body">
          <p className="ops-muted">{dialog.kind==='delete'?`将永久删除「${dialog.tenant!.name}」及其项目、账号和数据，此操作无法撤销。上传文件和会话数据将在后台清理。已投放到广告平台的广告不会被删除。`:dialog.kind==='rename'?'修改后将显示新的客户空间名称。':dialog.kind==='tenant'?'同时创建客户空间、首个项目和客户管理员。':dialog.kind==='admin'?`为「${dialog.tenant!.name}」创建独立的客户管理员账号。`:dialog.kind==='password'?`为 ${dialog.admin!.email} 设置新密码，已有登录会话将立即失效。`:`确认${dialog.admin!.active?'停用':'启用'}管理员 ${dialog.admin!.email}？${dialog.admin!.active?'停用后该账号将无法登录。':''}`}</p>
          {dialog.kind==='rename'&&<label>客户空间名称<input data-modal-autofocus name="name" required maxLength={255} defaultValue={dialog.tenant!.name} disabled={busy}/></label>}
          {dialog.kind==='delete'&&<label>输入客户空间名称确认删除<input data-modal-autofocus name="confirm_name" required autoComplete="off" disabled={busy}/></label>}
          {!['status','rename','delete'].includes(dialog.kind)&&<fieldset className="policy-fields ops-fields" disabled={busy}>
            {dialog.kind==='tenant'&&<><label>客户空间名称<input name="name" required maxLength={255} placeholder="例如：某某品牌"/></label><label>首个项目名称<input name="project_name" required maxLength={255} placeholder="例如：TikTok 广告投放"/></label><label>广告账户 ID（选填）<input name="account_id" maxLength={100} placeholder="暂无广告账户可留空"/></label></>}
            {dialog.kind!=='password'&&<label>管理员邮箱<input name="email" type="email" required maxLength={255} autoComplete="off"/></label>}
            <label>{dialog.kind==='password'?'新密码':'初始密码'}<input name="password" type="password" required minLength={16} maxLength={256} autoComplete="new-password" placeholder="至少 16 个字符"/></label><label>确认密码<input name="confirm" type="password" required minLength={16} maxLength={256} autoComplete="new-password"/></label>
          </fieldset>}
        </div>
        <footer className="modal-footer"><button type="button" className="ops-secondary" disabled={busy} onClick={()=>{setDialog(null);setError('')}}>取消</button><button type="submit" className="ops-primary" disabled={busy}>{busy?'正在保存…':dialog.kind==='delete'?'永久删除':dialog.kind==='rename'?'保存名称':dialog.kind==='tenant'?'确认开通':dialog.kind==='admin'?'确认添加':dialog.kind==='password'?'确认重置':dialog.admin!.active?'确认停用':'确认启用'}</button></footer>
      </form></Modal>}
  </>;
}
