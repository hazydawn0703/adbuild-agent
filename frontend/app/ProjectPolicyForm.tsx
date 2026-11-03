'use client';
import {useState} from 'react';
import {ShieldCheck} from 'lucide-react';
import StringListField from './StringListField';
import {objectiveOptions as objectives,beijingTime} from './project-form-values';
import type {Project,ProjectPolicy} from '../lib/types';

const clean=(values:string[])=>[...new Set(values.map(value=>value.trim()).filter(Boolean))];

export default function ProjectPolicyForm({project,busy,onSave,onCancel}:{project:Project;busy:boolean;onSave:(policy:ProjectPolicy)=>Promise<void>;onCancel:()=>void}) {
  const [policy,setPolicy]=useState<ProjectPolicy>(()=>project.policy?{...project.policy,version:project.policy.version+1}:{version:1,enabled:false,account_ids:project.account_id?[project.account_id]:[],objectives:['followers','video_views','traffic'],audiences:[],identities:[],currency:'USD',per_item_limit:'50',batch_limit:'500',cumulative_limit:'1000',valid_until:''});
  const [expiresAt,setExpiresAt]=useState(()=>beijingTime(project.policy?.valid_until||''));
  const [error,setError]=useState('');
  const update=<K extends keyof ProjectPolicy>(key:K,value:ProjectPolicy[K])=>setPolicy(current=>({...current,[key]:value}));
  async function submit(e:React.FormEvent<HTMLFormElement>) {
    e.preventDefault();setError('');
    const normalized={...policy,account_ids:clean(policy.account_ids),audiences:clean(policy.audiences),identities:clean(policy.identities),currency:policy.currency.trim().toUpperCase(),valid_until:expiresAt?`${expiresAt}+08:00`:''};
    if(!expiresAt||!Number.isFinite(Date.parse(normalized.valid_until))){setError('请填写有效的授权截止时间');return}
    if(policy.enabled&&(!normalized.account_ids.length||!policy.objectives.length||!normalized.audiences.length||!normalized.identities.length)){setError('启用前请至少添加一个广告账户、广告目标、受众模板和广告身份');return}
    if(policy.enabled&&Date.parse(normalized.valid_until)<=Date.now()){setError('启用授权时，截止时间必须晚于当前时间');return}
    for(const key of ['per_item_limit','batch_limit','cumulative_limit'] as const){
      const value=String(policy[key]).trim();
      if(!/^\d+(\.\d+)?$/.test(value)||!/[1-9]/.test(value)){setError('所有预算上限必须是大于 0 的金额');return}
      normalized[key]=value;
    }
    await onSave(normalized);
  }
  return <form className="policy-form modal-form" onSubmit={submit}><div className="modal-body">
    <div className="policy-intro"><ShieldCheck size={20}/><div><strong>{project.name}</strong><p>预授权限定可用账户、投放范围与预算。每次搭建仍需单独确认完整方案。</p></div></div>
    <fieldset className="policy-fields" disabled={busy}>
      <label className="policy-enable"><div><strong>启用项目预授权</strong><span>关闭后，本项目不允许执行自动投放。</span></div><input type="checkbox" role="switch" aria-label="启用项目预授权" checked={policy.enabled} onChange={e=>update('enabled',e.target.checked)}/></label>
      <section className="policy-section"><h3>授权范围</h3><p className="policy-help">以下均为允许名单；未填写的范围不会自动放开。</p>
        <StringListField label="广告账户" values={policy.account_ids} onChange={value=>update('account_ids',value)} required={policy.enabled} placeholder="输入广告账户 ID" emptyHint="未添加任何广告账户，表示不授权此范围。"/>
        <fieldset className="policy-objectives"><legend>允许的广告目标</legend><div>{objectives.map(item=><label key={item.value}><input type="checkbox" checked={policy.objectives.includes(item.value)} onChange={e=>update('objectives',e.target.checked?[...policy.objectives,item.value]:policy.objectives.filter(value=>value!==item.value))}/><span>{item.label}</span></label>)}</div></fieldset>
        <div className="policy-grid"><StringListField label="受众模板" values={policy.audiences} onChange={value=>update('audiences',value)} required={policy.enabled} placeholder="输入受众模板名称" emptyHint="未添加任何受众模板，表示不授权此范围。"/><StringListField label="广告身份" values={policy.identities} onChange={value=>update('identities',value)} required={policy.enabled} placeholder="输入广告身份 ID" emptyHint="未添加任何广告身份，表示不授权此范围。"/></div>
      </section>
      <section className="policy-section"><h3>预算与有效期</h3><div className="policy-grid">
        <label>预算币种<input aria-label="预算币种" list="policy-currencies" required pattern="[A-Z]{3}" maxLength={3} value={policy.currency} onChange={e=>update('currency',e.target.value.toUpperCase())}/><span className="policy-help">选择或输入三位币种代码。</span></label>
        <label>授权截止时间（北京时间）<input type="datetime-local" step="1" required value={expiresAt} onChange={e=>setExpiresAt(e.target.value)}/><span className="policy-help">UTC+8，按此时间停止授权。</span></label>
        {([{key:'per_item_limit',label:'单条预算上限'},{key:'batch_limit',label:'单批预算上限'},{key:'cumulative_limit',label:'累计预算上限'}] as const).map(item=><label key={item.key}>{item.label}<div className="policy-amount"><input aria-label={item.label} type="text" inputMode="decimal" required pattern="[0-9]+([.][0-9]+)?" value={policy[item.key]} onChange={e=>update(item.key,e.target.value)}/><span>{policy.currency||'币种'}</span></div></label>)}
        <div className="policy-reserved"><span>当前已预占金额</span><strong>{project.reserved} {project.policy?.currency||policy.currency}</strong><p className="policy-help">累计上限不能低于已预占金额。</p></div>
      </div><datalist id="policy-currencies">{['USD','CNY','EUR','GBP','HKD','SGD','JPY','AUD','CAD'].map(value=><option key={value} value={value}/>)}</datalist></section>
    </fieldset>
    </div>{error&&<p className="modal-error" role="alert">{error}</p>}
    <footer className="modal-footer"><button type="button" className="outline" disabled={busy} onClick={onCancel}>取消</button><button className="primary" type="submit" disabled={busy}>{busy?'正在保存…':'保存预授权'}</button></footer>
  </form>;
}
