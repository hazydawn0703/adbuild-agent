"""Reproduce a small, explicit set of schedule calculations from trusted evidence."""
from datetime import datetime,timedelta,timezone
import re
from .excel import date_value,zone_value
from .models import SourceRef


def integer(text):
    if text.isdigit():return int(text)
    digits={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if text in digits:return digits[text]
    if text=='十':return 10
    if text.count('十')==1:
        left,right=text.split('十')
        if (not left or left in digits) and (not right or right in digits):return digits.get(left,1)*10+digits.get(right,0)
    return None


def applicable_messages(state,row):
    """A group-specific statement never supplies a sibling's schedule."""
    plans=(state.get('draft') or {}).get('requirements',[])
    plan=next((r for r in plans if any(g['id']==row.get('id') for g in (r.get('structure') or {}).get('groups',[]))),None)
    names=[g.get('name','') for g in (plan or {}).get('structure',{}).get('groups',[])]
    for message in state.get('messages',[]):
        if message.get('role')!='user' or 'answers' in message:continue
        text=str(message.get('content',''))
        businesses=re.findall(r'(?<![A-Za-z0-9_])BR-[A-Za-z0-9_-]+',text)
        if businesses and row.get('business_no') not in businesses:continue
        mentioned=[name for name in names if name and re.search(r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])',text)]
        if mentioned and mentioned!=[row.get('name')]:continue
        yield message


def schedule_sources(state,row):
    """Return proposed values and server-owned calculation provenance; never mutate a draft."""
    if row.get('_entity_kind')!='adgroup':return {}
    messages=list(applicable_messages(state,row));duration=None;duration_message=None;relative=None
    number=r'[0-9一二两三四五六七八九十]+'
    for message in messages:
        text=message['content']
        if re.search(r'大约|左右|至少|最多|自然日|含开始|含当天|工作日',text):continue
        spans=re.findall(r'(?:跑|投放|持续|运行|为期)\s*('+number+r')\s*(天|小时)',text)
        amounts={(integer(n),unit) for n,unit in spans}
        if len(amounts)==1:
            amount,unit=next(iter(amounts))
            if amount is not None and 0<amount<=366:
                duration=timedelta(hours=amount*(24 if unit=='天' else 1));duration_message=message
        elif spans:duration=None;duration_message=None
        # The user's interpretation zone is separate from the account's display zone.
        clock=re.search(r'(明晚|今晚|后晚|明天|今天|后天)\s*(?:晚上|晚间)?\s*('+number+r')\s*点(?:\s*([0-5]?\d)\s*分?)?',text)
        if not clock:continue
        hour=integer(clock[2]);minute=int(clock[3] or 0)
        if hour is None or not 0<=hour<=23:continue
        evening='晚' in clock[1] or bool(re.search(r'(?:晚上|晚间)',clock[0]))
        if evening and not 1<=hour<=11:continue
        if evening:hour+=12
        if not evening and '上午' not in clock[0] and hour<13:continue
        zone='Asia/Shanghai' if re.search(r'上海(?:时区|时间)',text) else row.get('timezone','')
        anchor=message.get('business_time')
        if not anchor or not zone:continue
        try:
            point=datetime.fromisoformat(anchor.replace('Z','+00:00'))
            if point.tzinfo is None:continue
            local=point.astimezone(zone_value(zone));days={'明晚':1,'今晚':0,'后晚':2,'明天':1,'今天':0,'后天':2}[clock[1]]
            relative=(local+timedelta(days=days)).replace(hour=hour,minute=minute,second=0,microsecond=0)
            relative=(relative,message)
        except (ValueError,KeyError):continue
    results={};start=None;basis=[]
    if row.get('start_at') and row.get('sources',{}).get('start_at'):
        try:start=date_value(row['start_at'],row.get('timezone',''))
        except (ValueError,TypeError):pass
        source=row['sources']['start_at'];basis=[row['id']+':start_at']
        if source.get('message_id'):basis.append('message:'+source['message_id'])
    elif relative and not row.get('sources',{}).get('start_at'):
        start,message=relative
        basis=['message:'+message['id'],'business_clock:'+message['business_time'],'interpretation_timezone:'+str(start.tzinfo)]
        results['start_at']=(start,SourceRef(kind='calculated',file_id='',sheet='',cell='',raw_value=start.isoformat(),message_id=message['id'],derived_from=basis))
    if not start or not start.tzinfo or duration is None:return results
    # Explicit end instructions outrank duration completion; never replace an existing endpoint.
    if row.get('end_at') or row.get('sources',{}).get('end_at'):return results
    if any(re.search(r'结束|截止|end\s*(?:at|time)',m['content'],re.I) for m in messages):return results
    if any(a.get('field')=='end_at' and row['id'] in (a.get('target_ids') or []) for m in state.get('messages',[]) if m.get('role')=='user' for a in m.get('answers',[])):return results
    end=(start.astimezone(timezone.utc)+duration).astimezone(start.tzinfo)
    origin=[*basis,'message:'+duration_message['id'],'elapsed_seconds:'+str(int(duration.total_seconds()))]
    results['end_at']=(end,SourceRef(kind='calculated',file_id='',sheet='',cell='',raw_value=end.isoformat(),message_id=duration_message['id'],derived_from=origin))
    return results


def verified_schedule_source(state,row,field,value,offered=None):
    if field not in ('start_at','end_at'):return None
    candidate=schedule_sources(state,row).get(field)
    if candidate is None:return None
    expected,source=candidate
    try:actual=date_value(value,row.get('timezone',''))
    except (ValueError,TypeError):return None
    if actual!=expected:return None
    if offered:
        ref=SourceRef.model_validate(offered)
        messages={v.removeprefix('message:') for v in source.derived_from if v.startswith('message:')}
        if ref.file_id or ref.kind not in ('confirmed','calculated') or ref.message_id not in messages:return None
    return source


def schedule_suggestions(state):
    result=[]
    for raw in (state.get('draft') or {}).get('requirements',[]):
        if not raw.get('included',True):continue
        for group in (raw.get('structure') or {}).get('groups',[]):
            if not isinstance(group.get('id'),str) or not group['id']:continue
            owner={'business_no':raw.get('business_no',''),**group,'_entity_kind':'adgroup'}
            for field,(value,source) in schedule_sources(state,owner).items():
                result.append({'requirement_id':raw['id'],'target_id':group['id'],'field':field,'value':value.isoformat(),'source':source.model_dump(mode='json')})
    return result
