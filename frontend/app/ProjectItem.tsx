'use client';
import {useId,useRef,useState} from 'react';
import {MoreHorizontal,Pencil,Trash2,Archive} from 'lucide-react';
import type {Project} from '../lib/types';

export default function ProjectItem({project,selected,busy,onSelect,onAction}:{project:Project;selected:boolean;busy:boolean;onSelect:()=>void;onAction:(action:'rename'|'delete'|'archivedTasks')=>void}){
 const id=useId(),popover=useRef<HTMLDivElement>(null),trigger=useRef<HTMLButtonElement>(null);
 const [position,setPosition]=useState({top:0,left:0});
 function choose(action:'rename'|'delete'|'archivedTasks'){popover.current?.hidePopover();trigger.current?.focus();onAction(action)}
 return <div className={`project-row ${selected?'selected':''}`}>
  <button title={project.name} className={`project ${selected?'selected':''}`} onClick={onSelect}><span className="project-dot"/><span className="project-name">{project.name}</span></button>
  <button ref={trigger} className="icon-button project-menu-trigger" aria-label={`${project.name}的项目菜单`} popoverTarget={id} disabled={busy} onClick={e=>{const r=e.currentTarget.getBoundingClientRect();setPosition({top:Math.max(8,Math.min(r.bottom+4,window.innerHeight-144)),left:Math.max(8,Math.min(r.right-152,window.innerWidth-160))})}}><MoreHorizontal size={16}/></button>
  <div id={id} ref={popover} popover="auto" className="project-menu" style={position} aria-label={`${project.name}的项目操作`}>
   <button disabled={busy} onClick={()=>choose('archivedTasks')}><Archive size={14}/>已归档搭建任务</button>
   <button disabled={project.role!=='admin'||busy} onClick={()=>choose('rename')}><Pencil size={14}/>重命名</button>
   <button disabled={project.role!=='admin'||busy} onClick={()=>choose('delete')}><Trash2 size={14}/>删除项目</button>
  </div>
 </div>
}

