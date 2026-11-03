const prefix = '/apps/adbuild-agent/api/ops';

export async function opsRequest(path:string, method='GET', body?:unknown) {
  const response = await fetch(prefix+path, {
    method, credentials:'same-origin', cache:'no-store',
    headers:body === undefined ? {} : {'Content-Type':'application/json'},
    body:body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(()=>({detail:`请求失败（HTTP ${response.status}），请检查代理或服务状态后重试`}));
    if (response.status===401 && path!=='/login') window.location.replace('/apps/adbuild-agent/ops/login');
    throw new Error(typeof error.detail==='string' ? error.detail : '请检查输入内容，密码至少 16 位，邮箱和名称必须有效');
  }
  return response.json();
}
