'use client';
import {useEffect,useId,useRef} from 'react';
import {X} from 'lucide-react';

export default function Modal({title,onClose,busy=false,error='',className='',closeLabel='关闭窗口',children}:{title:React.ReactNode;onClose:()=>void;busy?:boolean;error?:string;className?:string;closeLabel?:string;children:React.ReactNode}) {
  const ref=useRef<HTMLDialogElement>(null),titleId=useId();
  useEffect(()=>{
    const dialog=ref.current!;
    const trigger=document.activeElement instanceof HTMLElement?document.activeElement:null;
    const previous=document.body.style.overflow;
    document.body.style.overflow='hidden';
    const viewport=window.visualViewport;
    function fitViewport(){
      dialog.style.setProperty('--modal-viewport-height',`${viewport?.height??window.innerHeight}px`);
      dialog.style.setProperty('--modal-viewport-top',`${viewport?.offsetTop??0}px`);
    }
    fitViewport();
    viewport?.addEventListener('resize',fitViewport);
    viewport?.addEventListener('scroll',fitViewport);
    window.addEventListener('resize',fitViewport);
    dialog.showModal();
    dialog.querySelector<HTMLElement>('[data-modal-autofocus]')?.focus();
    return()=>{viewport?.removeEventListener('resize',fitViewport);viewport?.removeEventListener('scroll',fitViewport);window.removeEventListener('resize',fitViewport);dialog.close();document.body.style.overflow=previous;if(trigger?.isConnected)trigger.focus({preventScroll:true})};
  },[]);
  return <dialog ref={ref} className={`modal ${className}`} aria-labelledby={titleId} onCancel={e=>{e.preventDefault();e.stopPropagation();if(!busy)onClose()}} onKeyDown={e=>{
    if(e.key!=='Tab'||(e.target as HTMLElement).closest('dialog')!==e.currentTarget)return;
    const controls=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button,input,select,textarea,a[href],[tabindex]')).filter(el=>el.tabIndex>=0&&!el.matches(':disabled')&&el.getClientRects().length>0);
    const first=controls[0],last=controls.at(-1);
    if(!first){e.preventDefault();return}
    if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}
    else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
  }}>
    <header className="modal-header"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" aria-label={closeLabel} disabled={busy} onClick={onClose}><X size={19}/></button></header>
    {error&&<p className="modal-error" role="alert">{error}</p>}
    {children}
  </dialog>;
}
