'use client';
import {useState} from 'react';
import {Settings2} from 'lucide-react';
import {beijingTime,objectiveOptions} from './project-form-values';
import type {Requirement} from '../lib/types';

const fieldNames=['name','account_id','objective','post_id','post_url','identity_id','budget','budget_mode','currency','timezone','start_at','end_at','audience','language','cta','landing_url'] as const;
type EditField=typeof fieldNames[number];
export type RequirementEditValues=Pick<Requirement,EditField>;

export default function RequirementEditForm({requirement,busy,readOnly=false,onSave,onCancel}:{requirement:Requirement;busy:boolean;readOnly?:boolean;onSave:(values:RequirementEditValues)=>Promise<void>;onCancel:()=>void}) {
  const [values,setValues]=useState(()=>Object.fromEntries(fieldNames.map(field=>[field,String(requirement[field]??'')])) as Record<EditField,string>);
  const [error,setError]=useState('');
  function update(field:EditField,value:string){setError('');setValues(current=>({...current,[field]:value}))}
  async function submit(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault();if(readOnly)return;
    setError('');
    const linked=values.post_url.match(/\/video\/(\d+)/)?.[1];
    if(linked&&values.post_id&&linked!==values.post_id){setError('帖子 ID 与链接中的视频 ID 不一致，请修改链接或清空旧链接后再保存。');return}
    await onSave({...values,objective:values.objective||null,budget:values.budget||null,budget_mode:values.budget_mode||null,start_at:values.start_at||null,end_at:values.end_at||null});
  }
  function dateField(field:'start_at'|'end_at',label:string){return <label>{label}<input aria-label={label} type="datetime-local" step="1" value={beijingTime(values[field])} onChange={e=>update(field,e.target.value?`${e.target.value}+08:00`:'')}/><span className="policy-help">北京时间（UTC+8）；留空表示待补充。</span></label>}
  return <form className="policy-form modal-form" onSubmit={submit}>
    {error&&<p className="modal-error" role="alert">{error}</p>}
    <div className="modal-body">
      <div className="policy-intro"><Settings2 size={20}/><div><strong>{requirement.name||'广告需求配置'}</strong><p>修改后生成新版本，原有搭建确认会失效。保存不会创建广告或启用投放。</p></div></div>
      <fieldset className="policy-fields" disabled={busy||readOnly}>
        <section className="policy-section requirement-section"><h3>投放与账户</h3><div className="policy-grid">
          <label>广告计划名称<input value={values.name} onChange={e=>update('name',e.target.value)}/></label>
          <label>广告账户<input value={values.account_id} onChange={e=>update('account_id',e.target.value)}/></label>
          <label>广告目标<select aria-label="广告目标" value={values.objective} onChange={e=>update('objective',e.target.value)}><option value="">待补充</option>{objectiveOptions.map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <label>广告身份<input value={values.identity_id} onChange={e=>update('identity_id',e.target.value)}/></label>
          <label>帖子 ID<input inputMode="numeric" pattern="[0-9]*" value={values.post_id} onChange={e=>update('post_id',e.target.value)} placeholder="待补充"/></label>
          <label>TikTok 帖子链接<input type="url" value={values.post_url} onChange={e=>update('post_url',e.target.value)} placeholder="https://www.tiktok.com/@brand/video/…"/></label>
        </div></section>
        <section className="policy-section"><h3>预算与排期</h3><div className="policy-grid">
          <label>预算金额<input inputMode="decimal" pattern="[0-9]+([.][0-9]+)?" value={values.budget} onChange={e=>update('budget',e.target.value)} placeholder="待补充"/></label>
          <label>预算类型<select aria-label="预算类型" value={values.budget_mode} onChange={e=>update('budget_mode',e.target.value)}><option value="">待补充</option><option value="lifetime">总预算</option><option value="daily">日预算</option></select></label>
          <label>币种<input list="requirement-currencies" pattern="[A-Z]{3}" maxLength={3} value={values.currency} onChange={e=>update('currency',e.target.value.toUpperCase())}/></label>
          <label>排期时区<input list="requirement-timezones" value={values.timezone} onChange={e=>update('timezone',e.target.value)} placeholder="例如：UTC 或 Asia/Shanghai"/></label>
          {dateField('start_at','开始时间（北京时间）')}{dateField('end_at','结束时间（北京时间）')}
        </div></section>
        <section className="policy-section"><h3>受众与落地页</h3><div className="policy-grid">
          <label>受众模板<input value={values.audience} onChange={e=>update('audience',e.target.value)}/></label>
          <label>语言<input list="requirement-languages" value={values.language} onChange={e=>update('language',e.target.value)} placeholder="例如：zh 或 en"/></label>
          <label>行动号召<input value={values.cta} onChange={e=>update('cta',e.target.value)}/></label>
          <label>落地页<input inputMode="url" value={values.landing_url} onChange={e=>update('landing_url',e.target.value)} placeholder="https://"/></label>
        </div></section>
      </fieldset>
      <datalist id="requirement-currencies">{['USD','CNY','EUR','GBP','HKD','SGD','JPY','AUD','CAD'].map(value=><option key={value} value={value}/>)}</datalist>
      <datalist id="requirement-timezones">{['UTC','Asia/Shanghai','Asia/Hong_Kong','Asia/Singapore','America/New_York','America/Los_Angeles','Europe/London'].map(value=><option key={value} value={value}/>)}</datalist>
      <datalist id="requirement-languages">{['zh','en','ja','ko','es','fr','de'].map(value=><option key={value} value={value}/>)}</datalist>
    </div>
    <footer className="modal-footer"><button type="button" className="outline" disabled={busy} onClick={onCancel}>取消</button><button type="submit" className="primary" disabled={busy||readOnly}>{busy?'正在保存…':'保存配置'}</button></footer>
  </form>;
}
