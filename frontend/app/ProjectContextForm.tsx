'use client';
import {useState} from 'react';
import {Plus,X,Settings2} from 'lucide-react';
import StringListField from './StringListField';
import {objectiveOptions,beijingTime} from './project-form-values';
import type {ProjectContext,Project} from '../lib/types';

const fields={account_id:'广告账户',business_no:'需求序号',name:'广告计划名称',post_url:'帖子链接',post_id:'帖子 ID',objective:'广告目标',budget:'预算金额',budget_mode:'预算类型',currency:'币种',start_at:'开始时间',end_at:'结束时间',timezone:'排期时区',audience:'受众模板',language:'语言',identity_id:'广告身份',cta:'行动号召',landing_url:'落地页'} as const;
type DefaultField=keyof typeof fields;
type DefaultRow={id:string;field:DefaultField;value:string};
type TermRow={id:string;term:string;meaning:string};

function DefaultInput({row,onChange}:{row:DefaultRow;onChange:(value:string)=>void}) {
  const label=`默认${fields[row.field]}`;
  if(row.field==='objective'||row.field==='budget_mode')return <select aria-label={label} required value={row.value} onChange={e=>onChange(e.target.value)}><option value="">请选择</option>{(row.field==='objective'?objectiveOptions:[{value:'lifetime',label:'总预算'},{value:'daily',label:'日预算'}]).map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select>;
  if(row.field==='start_at'||row.field==='end_at'){
    const valid=!row.value||Number.isFinite(Date.parse(row.value));
    return <div><input aria-label={label} type="datetime-local" step="1" required value={valid?beijingTime(row.value):''} onChange={e=>onChange(e.target.value?`${e.target.value}+08:00`:'')}/><span className="policy-help">{valid?'按北京时间（UTC+8）编辑。':`现有时间「${row.value}」无法识别，请重新选择。`}</span></div>;
  }
  if(row.field==='budget')return <input aria-label={label} type="text" inputMode="decimal" pattern="[0-9]+([.][0-9]+)?" required value={row.value} onChange={e=>onChange(e.target.value)} placeholder="输入预算金额"/>;
  if(row.field==='currency')return <input aria-label={label} list="context-currencies" pattern="[A-Z]{3}" maxLength={3} required value={row.value} onChange={e=>onChange(e.target.value.toUpperCase())} placeholder="例如：USD"/>;
  if(row.field==='timezone')return <input aria-label={label} list="context-timezones" required value={row.value} onChange={e=>onChange(e.target.value)} placeholder="例如：Asia/Shanghai"/>;
  return <input aria-label={label} required value={row.value} onChange={e=>onChange(e.target.value)} placeholder={`输入${fields[row.field]}`}/>;
}

export default function ProjectContextForm({project,initial,busy,onSave,onCancel}:{project:Project;initial:ProjectContext;busy:boolean;onSave:(context:ProjectContext)=>Promise<void>;onCancel:()=>void}) {
  const [accounts,setAccounts]=useState(initial.account_ids),[objectives,setObjectives]=useState(initial.objectives);
  const [defaults,setDefaults]=useState<DefaultRow[]>(()=>Object.entries(initial.defaults).map(([field,value])=>({id:crypto.randomUUID(),field:field as DefaultField,value})));
  const [terms,setTerms]=useState<TermRow[]>(()=>Object.entries(initial.terms).map(([term,meaning])=>({id:crypto.randomUUID(),term,meaning})));
  const [error,setError]=useState('');
  const available=(Object.keys(fields) as DefaultField[]).filter(field=>!defaults.some(row=>row.field===field));
  function patchDefault(id:string,patch:Partial<DefaultRow>) {setDefaults(rows=>rows.map(row=>row.id===id?{...row,...patch}:row))}
  async function submit(e:React.FormEvent<HTMLFormElement>) {
    e.preventDefault();setError('');
    if(defaults.some(row=>!row.value.trim())){setError('请填写所有默认值，或移除不需要的字段');return}
    if(defaults.some(row=>(row.field==='start_at'||row.field==='end_at')&&!Number.isFinite(Date.parse(row.value)))){setError('请填写有效的默认时间');return}
    if(defaults.some(row=>row.field==='budget'&&(!/^\d+(\.\d+)?$/.test(row.value)||!/[1-9]/.test(row.value)))){setError('默认预算必须是大于 0 的金额');return}
    if(new Set(defaults.map(row=>row.field)).size!==defaults.length){setError('同一个默认字段只能设置一次');return}
    if(terms.some(row=>!row.term.trim()||!row.meaning.trim())){setError('请填写术语和对应含义，或移除不需要的术语');return}
    if(new Set(terms.map(row=>row.term)).size!==terms.length){setError('术语名称不能重复');return}
    await onSave({...initial,account_ids:[...new Set(accounts.map(value=>value.trim()).filter(Boolean))],objectives,defaults:Object.fromEntries(defaults.map(row=>[row.field,row.value])),terms:Object.fromEntries(terms.map(row=>[row.term,row.meaning]))});
  }
  return <form className="policy-form modal-form" onSubmit={submit}><div className="modal-body">
    <div className="policy-intro"><Settings2 size={20}/><div><strong>{project.name}</strong><p>仅补齐需求中尚未填写的字段，不覆盖已有值。保存后的配置用于新建任务，当前任务保留原配置。</p></div></div>
    <fieldset className="policy-fields" disabled={busy}>
      <section className="policy-section context-first-section"><h3>适用范围</h3>
        <StringListField label="适用广告账户" values={accounts} onChange={setAccounts} placeholder="输入广告账户 ID" emptyHint="留空时适用于本项目的所有广告账户。"/>
        <fieldset className="policy-objectives"><legend>适用广告目标</legend><div>{objectiveOptions.map(option=><label key={option.value}><input type="checkbox" checked={objectives.includes(option.value)} onChange={e=>setObjectives(e.target.checked?[...objectives,option.value]:objectives.filter(value=>value!==option.value))}/><span>{option.label}</span></label>)}</div><p className="policy-help">不勾选时适用于所有广告目标。</p></fieldset>
      </section>
      <section className="policy-section"><h3>默认字段</h3><p className="policy-help">添加需要自动补齐的字段。未添加的字段将保持未设置状态。</p>
        <div className="context-rows">{defaults.map((row,index)=><div className="context-default-row" key={row.id}>
          <label>字段<select aria-label={`默认字段 ${index+1}`} value={row.field} onChange={e=>patchDefault(row.id,{field:e.target.value as DefaultField,value:''})}>{(Object.keys(fields) as DefaultField[]).map(field=><option key={field} value={field} disabled={field!==row.field&&defaults.some(item=>item.field===field)}>{fields[field]}</option>)}</select></label>
          <label>默认值<DefaultInput row={row} onChange={value=>patchDefault(row.id,{value})}/></label>
          <button type="button" className="icon-button" aria-label={`移除默认${fields[row.field]}`} onClick={()=>setDefaults(rows=>rows.filter(item=>item.id!==row.id))}><X size={15}/></button>
        </div>)}</div>
        <button type="button" className="outline policy-add" disabled={!available.length} onClick={()=>setDefaults(rows=>[...rows,{id:crypto.randomUUID(),field:available[0],value:''}])}><Plus size={14}/>添加默认字段</button>
      </section>
      <section className="policy-section"><h3>项目术语</h3><p className="policy-help">说明客户简称或项目专用词的含义，帮助理解需求。</p>
        <div className="context-rows">{terms.map((row,index)=><div className="context-term-row" key={row.id}>
          <label>术语<input aria-label={`术语 ${index+1}`} required value={row.term} onChange={e=>setTerms(rows=>rows.map(item=>item.id===row.id?{...item,term:e.target.value}:item))} placeholder="例如：HQ"/></label>
          <label>含义<input aria-label={`术语含义 ${index+1}`} required value={row.meaning} onChange={e=>setTerms(rows=>rows.map(item=>item.id===row.id?{...item,meaning:e.target.value}:item))} placeholder="输入此术语的含义"/></label>
          <button type="button" className="icon-button" aria-label={`移除术语 ${index+1}`} onClick={()=>setTerms(rows=>rows.filter(item=>item.id!==row.id))}><X size={15}/></button>
        </div>)}</div>
        <button type="button" className="outline policy-add" onClick={()=>setTerms(rows=>[...rows,{id:crypto.randomUUID(),term:'',meaning:''}])}><Plus size={14}/>添加术语</button>
      </section>
    </fieldset>
    <datalist id="context-currencies">{['USD','CNY','EUR','GBP','HKD','SGD','JPY','AUD','CAD'].map(value=><option key={value} value={value}/>)}</datalist>
    <datalist id="context-timezones">{['UTC','Asia/Shanghai','Asia/Hong_Kong','Asia/Singapore','America/New_York','America/Los_Angeles','Europe/London'].map(value=><option key={value} value={value}/>)}</datalist>
    </div>{error&&<p className="modal-error" role="alert">{error}</p>}
    <footer className="modal-footer"><button type="button" className="outline" disabled={busy} onClick={onCancel}>取消</button><button type="submit" className="primary" disabled={busy}>{busy?'正在保存…':'保存默认配置'}</button></footer>
  </form>;
}
