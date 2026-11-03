'use client';
export type Question={id:string;field:string;question:string;requirement_ids:string[]};
export default function Clarifications({questions,answers,onChange,onSave,disabled}:{questions:Question[];answers:Record<string,string>;onChange:(answers:Record<string,string>)=>void;onSave:()=>void;disabled:boolean}){
 return <section aria-label="本批待补充信息">{questions.map(q=><div className="chat-message" key={q.id}><label><strong>{q.question}</strong><p>影响 {q.requirement_ids.length} 条需求 · {q.field}</p><input disabled={disabled} aria-label={q.question} value={answers[q.id]||''} onChange={e=>onChange({...answers,[q.id]:e.target.value})}/></label></div>)}{Object.keys(answers).length>0&&<button disabled={disabled} onClick={onSave}>保存本批回答</button>}</section>
}
