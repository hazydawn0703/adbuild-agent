'use client';
import {ChevronDown, Plus, Settings2} from 'lucide-react';

type Props = {
  tasks:{id:string;data:{name:string}}[];
  value:string;
  selected:boolean;
  busy:boolean;
  readOnly:boolean;
  admin:boolean;
  onSelect:(id:string)=>void;
  onCreate:()=>void;
  onSettings:()=>void;
};

export default function TaskToolbar({tasks,value,selected,busy,readOnly,admin,onSelect,onCreate,onSettings}:Props) {
  return <div className="task-toolbar">
    <label className="task-picker"><span>当前任务</span><div>
      <select aria-label="选择搭建任务" disabled={!selected||busy||!tasks.length} value={value} onChange={e=>onSelect(e.target.value)}>
        <option value="">{selected?'创建或选择搭建任务':'请先选择客户项目'}</option>
        {tasks.map(task=><option key={task.id} value={task.id}>{task.data.name}</option>)}
      </select><ChevronDown size={14}/>
    </div></label>
    <div className="task-actions">
      <button className="outline" disabled={!selected||busy||readOnly} onClick={onCreate}><Plus size={14}/>新建搭建任务</button>
      <button className="outline" disabled={!selected||busy||!admin} onClick={onSettings}><Settings2 size={14}/>项目默认配置</button>
    </div>
  </div>;
}
