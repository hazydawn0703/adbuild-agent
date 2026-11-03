import {Plus,X} from 'lucide-react';

export default function StringListField({label,values,onChange,required=false,placeholder,emptyHint}:{label:string;values:string[];onChange:(values:string[])=>void;required?:boolean;placeholder:string;emptyHint:string}) {
  return <fieldset className="policy-list"><legend>{label}</legend>
    <div className="policy-list-rows">{values.map((value,index)=><div className="policy-list-row" key={index}>
      <input aria-label={`${label} ${index+1}`} value={value} required={required} placeholder={placeholder} onChange={e=>onChange(values.map((item,n)=>n===index?e.target.value:item))}/>
      <button type="button" className="icon-button" aria-label={`移除${label} ${index+1}`} onClick={()=>onChange(values.filter((_,n)=>n!==index))}><X size={15}/></button>
    </div>)}</div>
    <button type="button" className="outline policy-add" onClick={()=>onChange([...values,''])}><Plus size={14}/>添加{label}</button>
    {!values.length&&<p className="policy-help">{emptyHint}</p>}
  </fieldset>;
}
