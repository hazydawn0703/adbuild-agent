import type {Metadata} from 'next';
import './ops.css';

export const metadata: Metadata = {title:'平台管理 · Adbuild', robots:{index:false,follow:false}};
export default function OpsLayout({children}:{children:React.ReactNode}) {
  return <div className="ops-root">{children}</div>;
}
