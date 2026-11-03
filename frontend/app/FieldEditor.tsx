'use client';
export default function FieldEditor({value,onChange,labels}:{value:string;onChange:(v:string)=>void;labels:Record<string,string>}){
 const fields=JSON.parse(value) as Record<string,string|null>;
 const choices:Record<string,string[]>={objective:['followers','video_views','traffic'],budget_mode:['lifetime','daily']};
 const update=(key:string,v:string)=>onChange(JSON.stringify({...fields,[key]:v},null,2));
 return <div>{Object.entries(labels).map(([key,label])=><label key={key}>{label}{choices[key]?<select aria-label={label} value={fields[key]||''} onChange={e=>update(key,e.target.value)}><option value="">待补充</option>{choices[key].map(v=><option key={v}>{v}</option>)}</select>:<input aria-label={label} value={fields[key]||''} onChange={e=>update(key,e.target.value)} placeholder={key.endsWith('_at')?'YYYY-MM-DDTHH:mm:ss+时区偏移':''}/>}</label>)}</div>
}
