export const objectiveOptions=[{value:'followers',label:'涨粉互动'},{value:'video_views',label:'视频观看'},{value:'traffic',label:'流量引导'}] as const;

export function beijingTime(value:string) {
  return value?new Date(new Date(value).getTime()+8*3600000).toISOString().slice(0,19):'';
}
