'use client';
import Link from 'next/link';
import {usePathname} from 'next/navigation';
export default function OpsNav(){
 const path=usePathname();
 if(path.endsWith('/login'))return null;
 return <nav className="ops-nav" aria-label="平台管理导航">{[['/ops','客户空间管理'],['/ops/usage','任务用量信息']].map(([href,label])=><Link key={href} href={href} aria-current={path===href?'page':undefined}>{label}</Link>)}</nav>;
}
