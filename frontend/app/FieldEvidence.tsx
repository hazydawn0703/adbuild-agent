'use client';
import type {Ref} from '../lib/types';
export default function FieldEvidence({source,state}:{source?:Ref;state?:string}){
 if(!source)return <small>缺失 · 待补充</small>;
 const label=state==='conflict'?'冲突 · 待澄清':state==='inferred'?'推断 · 待采纳':source.kind==='history'?'历史推断':source.kind==='confirmed'?'明确 · 人工确认':source.kind==='template'?'明确 · 项目模板':'明确 · Excel';
 return <details><summary>{label}</summary><p>{source.sheet} {source.cell}</p><p>{source.raw_value}</p>{source.file_id&&<small>来源 {source.file_id}</small>}</details>
}
