export const origin = '/apps/adbuild-agent/api';
export async function api(path:string,token:string,init:RequestInit={}) {
  const headers = new Headers(init.headers);
  headers.set('Authorization',`Bearer ${token}`);
  if(init.body && !(init.body instanceof FormData)) headers.set('Content-Type','application/json');
  const r=await fetch(origin+path,{...init,headers});
  if(!r.ok) { const e=await r.json().catch(()=>({detail:'服务连接失败'})); throw new Error(typeof e.detail==='string'?e.detail:JSON.stringify(e.detail)); }
  return r.json();
}
