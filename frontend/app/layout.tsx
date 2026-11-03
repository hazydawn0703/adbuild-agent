import type { Metadata } from 'next';
import './style.css';
import './review.css';
import './modal.css';
export const metadata: Metadata = {
  title:'Adbuild · 广告搭建工作台',
  description:'从投放需求到 TikTok 广告搭建',
  icons: {
    icon: [
      {url:'/apps/adbuild-agent/brand/adbuild-icon.svg', type:'image/svg+xml'},
      {url:'/apps/adbuild-agent/brand/adbuild-icon-32.png', type:'image/png', sizes:'32x32'},
    ],
    shortcut:'/apps/adbuild-agent/brand/adbuild-icon.svg',
    apple: {url:'/apps/adbuild-agent/brand/adbuild-apple-touch-icon.png', sizes:'180x180', type:'image/png'},
  },
};
export default function Layout({children}:{children:React.ReactNode}) { return <html lang="zh-CN"><body>{children}</body></html> }
