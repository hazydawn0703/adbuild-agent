import type { Metadata } from 'next';
import './style.css';
export const metadata: Metadata = { title:'Adbuild · 广告搭建工作台', description:'从投放需求到 TikTok 广告搭建' };
export default function Layout({children}:{children:React.ReactNode}) { return <html lang="zh-CN"><body>{children}</body></html> }
