'use client';
import {useState} from 'react';
import {Plus} from 'lucide-react';

import Modal from './Modal';
import type {Structure,StructureGroup as Group} from '../lib/types';
type Material=Group['ads'][number];
type Base={id:string;name:string;audience:string;language:string;budget:string|null;budget_mode:string|null;currency:string;structure?:Structure|null};
export default function StructureEditor({row,busy,onClose,onSubmit}:{row:Base;busy:boolean;onClose:()=>void;onSubmit:(value:Structure)=>Promise<void>}){
 const [value,setValue]=useState<Structure>(()=>row.structure?structuredClone(row.structure):{budget_level:'campaign',groups:[{id:crypto.randomUUID(),name:'',audience:null,language:null,ads:[]}]});
 const [error,setError]=useState('');
 function group(index:number,patch:Partial<Group>){setValue(v=>({...v,groups:v.groups.map((g,i)=>i===index?{...g,...patch}:g)}))}
 function material(gi:number,ai:number,patch:Partial<Material>){group(gi,{ads:value.groups[gi].ads.map((a,i)=>i===ai?{...a,...patch}:a)})}
 return <Modal title="调整广告组与素材结构" closeLabel="关闭结构编辑" className="policy-modal" busy={busy} error={error} onClose={onClose}>
  <form className="policy-form modal-form" onSubmit={async e=>{e.preventDefault();setError('');try{await onSubmit(value)}catch(error){setError(error instanceof Error?error.message:'保存失败')}}}>
   <div className="modal-body"><p className="login-description">需求总预算 {row.budget||'待补充'} {row.currency}。保存后先生成待采纳提案，采纳后才能确认完整方案。</p>
   <fieldset className="policy-fields" disabled={busy}>
   <label>预算层级<select aria-label="结构预算层级" value={value.budget_level} disabled={busy} onChange={e=>setValue({...value,budget_level:e.target.value as Structure['budget_level'],groups:value.groups.map(g=>({...g,budget:e.target.value==='campaign'?null:{amount:null,allocation_ratio:null}}))})}><option value="campaign">计划预算（广告组共享）</option><option value="adgroup">广告组预算（合计等于需求总额）</option></select></label>
   {value.groups.map((g,gi)=><fieldset key={g.id} className="structure-group"><legend>广告组 {gi+1}</legend><div className="policy-grid">
    <label>组名称<input disabled={busy} value={g.name||''} placeholder="留空使用需求名称" onChange={e=>group(gi,{name:e.target.value})}/></label>
    <label>受众<input disabled={busy} value={g.audience??''} placeholder='待补充' onChange={e=>group(gi,{audience:e.target.value||null})}/></label>
    <label>语言<input disabled={busy} value={g.language??''} placeholder='例如 en 或 all' onChange={e=>group(gi,{language:e.target.value||null})}/></label>
    <label>组开始时间<input disabled={busy} value={g.start_at??''} placeholder="2028-01-10T09:00:00+08:00" onChange={e=>group(gi,{start_at:e.target.value||null})}/></label>
    <label>组结束时间<input disabled={busy} value={g.end_at??''} placeholder="2028-01-15T09:00:00+08:00" onChange={e=>group(gi,{end_at:e.target.value||null})}/></label>
    <label>组时区<input disabled={busy} value={g.timezone??''} placeholder="例如 Asia/Shanghai 或 UTC" onChange={e=>group(gi,{timezone:e.target.value})}/></label>
    {value.budget_level==='adgroup'&&<><label>分配方式<select disabled={busy} value={g.budget?.allocation_ratio!=null?'ratio':'amount'} onChange={e=>group(gi,{budget:e.target.value==='ratio'?{allocation_ratio:'',amount:null}:{amount:'',allocation_ratio:null}})}><option value="amount">指定金额</option><option value="ratio">指定权重（如 1:1:1）</option></select></label><label>{g.budget?.allocation_ratio!=null?'分配权重（正数）':'组预算金额'}<input disabled={busy} inputMode="decimal" value={g.budget?.allocation_ratio??g.budget?.amount??''} onChange={e=>group(gi,{budget:g.budget?.allocation_ratio!=null?{allocation_ratio:e.target.value,amount:null}:{amount:e.target.value,allocation_ratio:null}})}/></label></>}
   </div><p>每组须填写带时差的完整开始、结束时间及明确时区，并至少添加一个素材。</p>
   {g.ads.map((ad,ai)=><fieldset key={ad.id}><legend>素材 {ai+1}</legend><div className="policy-grid"><label>素材类型<select disabled={busy} value={ad.kind} onChange={e=>material(gi,ai,{kind:e.target.value as Material['kind'],authorization_code:'',post_id:'',post_url:''})}><option value="post">已有 TikTok 帖子</option><option value="authorization_code">Spark 授权码</option><option value="unknown">尚未明确</option></select></label><label>广告名称<input disabled={busy} value={ad.name||''} onChange={e=>material(gi,ai,{name:e.target.value})}/></label>
    {ad.kind==='authorization_code'?<label>素材授权码<input disabled={busy} type="password" autoComplete="off" value={ad.authorization_code||''} onChange={e=>material(gi,ai,{authorization_code:e.target.value})}/></label>:<><label>帖子 ID<input disabled={busy} value={ad.post_id||''} placeholder="待补充" onChange={e=>material(gi,ai,{post_id:e.target.value})}/></label><label>TikTok 帖子链接<input disabled={busy} value={ad.post_url||''} placeholder="待补充" onChange={e=>material(gi,ai,{post_url:e.target.value})}/></label></>}
    <label>广告身份<input disabled={busy} value={ad.identity_id||''} placeholder="待补充" onChange={e=>material(gi,ai,{identity_id:e.target.value})}/></label><label>CTA<input disabled={busy} value={ad.cta||''} placeholder="待补充" onChange={e=>material(gi,ai,{cta:e.target.value})}/></label><label>落地页<input disabled={busy} value={ad.landing_url||''} placeholder="待补充" onChange={e=>material(gi,ai,{landing_url:e.target.value})}/></label></div><button type="button" disabled={busy} onClick={()=>group(gi,{ads:g.ads.filter((_,i)=>i!==ai)})}>移除此素材</button></fieldset>)}
   <div className="structure-actions"><button type="button" disabled={busy||g.ads.length>=50} onClick={()=>group(gi,{ads:[...g.ads,{id:crypto.randomUUID(),kind:'post'}]})}><Plus size={12}/>添加素材</button><button type="button" disabled={busy||value.groups.length===1} onClick={()=>setValue({...value,groups:value.groups.filter((_,i)=>i!==gi)})}>移除此广告组</button></div></fieldset>)}
   <button type="button" disabled={busy||value.groups.length>=50} onClick={()=>setValue({...value,groups:[...value.groups,{id:crypto.randomUUID(),name:'',audience:null,language:null,budget:value.budget_level==='adgroup'?{amount:null,allocation_ratio:null}:null,ads:[]}]})}><Plus size={13}/>添加广告组</button>
   <p>平台能力和素材权限仍需后台核实。此操作只生成方案提案。</p></fieldset></div><footer className="modal-footer"><button type="button" className="outline" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy} type="submit">生成结构修改提案</button></footer>
  </form>
 </Modal>
}

