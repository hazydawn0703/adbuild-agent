'use client';
import {useState} from 'react';
import {LockKeyhole} from 'lucide-react';
import BrandMark from '../../BrandMark';
import {opsRequest} from '../client';

export default function OpsLogin() {
  const [password,setPassword]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  return <main className="ops-login"><section className="ops-login-card">
    <a className="ops-brand" href="/apps/adbuild-agent"><BrandMark size={38}/>adbuild</a>
    <div className="ops-lock"><LockKeyhole size={24}/></div>
    <p className="ops-eyebrow">PLATFORM OPERATIONS</p><h1>验证平台管理员身份</h1>
    <p className="ops-muted">输入平台管理密码后，管理客户空间和管理员账号。</p>
    <form onSubmit={async e=>{e.preventDefault();setBusy(true);setError('');try{await opsRequest('/login','POST',{password});setPassword('');window.location.replace('/apps/adbuild-agent/ops');}catch(e){setError((e as Error).message);setBusy(false)}}}>
      <label>平台管理密码<input type="password" autoComplete="current-password" required maxLength={256} value={password} onChange={e=>setPassword(e.target.value)} autoFocus/></label>
      {error&&<p className="ops-error" role="alert">{error}</p>}
      <button className="ops-primary" disabled={busy}>{busy?'正在验证…':'验证并进入'}</button>
    </form><p className="ops-footnote">客户管理员账号仅用于客户工作台，不能进入平台后台。</p>
  </section></main>;
}
