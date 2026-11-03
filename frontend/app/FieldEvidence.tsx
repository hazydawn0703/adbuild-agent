'use client';
import type {Ref} from '../lib/types';
export default function FieldEvidence({source,state}:{source?:Ref&{accepted_by?:string|null;accepted_at?:string|null};state?:string}){
 if(!source)return <small className="field-source">缺失 · 待补充</small>;
 const label=state==='conflict'?'冲突 · 待澄清':state==='inferred'&&!source.accepted_at?'推断 · 待采纳':source.kind==='history'?`历史依据${source.accepted_at?' · 已采纳':''}`:source.kind==='confirmed'?'用户输入':source.kind==='template'?'项目模板':'Excel 原文';
 return <details className="field-source"><summary>{label}</summary>{(source.sheet||source.cell)&&<p>{source.sheet} {source.cell}</p>}<p>{source.raw_value}</p>{source.context_version!=null&&<p>项目模板 v{source.context_version}</p>}{source.accepted_at&&<p>采纳人 {source.accepted_by} · {new Date(source.accepted_at).toLocaleString('zh-CN')}</p>}{source.message_id&&<a href={`#message-${source.message_id}`}>查看关联消息</a>}{source.file_id&&<small>来源文件 {source.file_id}</small>}</details>
}
