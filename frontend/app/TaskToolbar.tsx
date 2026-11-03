'use client';
import {ChevronDown, Plus, Archive, ArchiveRestore, ListTodo} from 'lucide-react';

type Props = {
  tasks:{id:string;data:{name:string}}[];
  value:string;
  selected:boolean;
  busy:boolean;
  readOnly:boolean;
  showArchived:boolean;
  archived:boolean;
  canArchive:boolean;
  onSelect:(id:string)=>void;
  onCreate:()=>void;
  onToggleArchived:()=>void;
  onArchive:()=>void;
};

export default function TaskToolbar({tasks,value,selected,busy,readOnly,showArchived,archived,canArchive,onSelect,onCreate,onToggleArchived,onArchive}:Props) {
  return <div className="task-toolbar">
    <label className="task-picker"><span>当前任务</span><div>
      <select aria-label="选择搭建任务" disabled={!selected||busy||!tasks.length} value={value} onChange={e=>onSelect(e.target.value)}>
        <option value="">{selected?'创建或选择搭建任务':'请先选择客户项目'}</option>
        {tasks.map(task=><option key={task.id} value={task.id}>{task.data.name}</option>)}
      </select><ChevronDown size={14}/>
    </div></label>
    <div className="task-actions">
      <button className="outline" disabled={!selected||busy} onClick={onToggleArchived}>{showArchived?<ListTodo size={14}/>:<Archive size={14}/>}<span>{showArchived?'查看进行中的任务':'查看已归档任务'}</span></button>
      <button className="outline" disabled={!canArchive||busy} onClick={onArchive}>{archived?<ArchiveRestore size={14}/>:<Archive size={14}/>}<span>{archived?'恢复任务':'归档任务'}</span></button>
      <button className="outline" disabled={!selected||busy||readOnly} onClick={onCreate}><Plus size={14}/>新建搭建任务</button>
    </div>
  </div>;
}
