"""Generate new private synthetic XLSX inputs only after fixing an application commit."""
import argparse
import json
import random
import secrets
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from openpyxl import Workbook


HEADERS={'record_key':'Record Key','platform':'Platform','account_id':'Account ID','business_no':'Business No',
 'name':'Campaign Name','group':'Ad Group Name','level':'Budget Level','campaign_budget':'Campaign Budget',
 'group_budget':'Ad Group Budget','post_id':'Post ID','post_url':'Post URL','objective':'Objective',
 'mode':'Budget Mode','currency':'Currency','start':'Start Time','end':'End Time','tz':'Timezone',
 'audience':'Audience','language':'Languages','identity':'Identity ID','cta':'CTA','url':'Landing URL'}


def generate(output,commit):
    root=Path(output);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    inputs=root/'inputs';inputs.mkdir();oracle={};contexts={};rng=random.Random(secrets.randbits(128))
    for index in range(90):
        case=f'N{index+1:03d}';family=index%6 if index<60 else ('scope','conflict','precision')[(index-60)//10]
        business='B-'+secrets.token_hex(4);name='Launch-'+secrets.token_hex(3)
        account='71'+str(secrets.randbelow(10**17)).zfill(17);identity='72'+str(secrets.randbelow(10**17)).zfill(17)
        goal=('followers','video_views','traffic')[index%3];level='campaign' if family in (0,3,4,'scope','conflict','precision') else 'adgroup'
        groups=3 if family==5 else 1 if family=='precision' else 2
        posts_per_group=2 if family in (0,1,3,4) else 1
        amount=Decimal('100.01') if family==5 else Decimal(rng.randint(50,300))+Decimal(rng.randint(0,99))/100
        mode='daily' if family==1 else 'lifetime'
        goals=['followers','traffic'] if family==2 else [goal]
        rows=[];plans=[];normal={};defaults={};clarify=None
        for objective in goals:
            expected_groups=[]
            for g in range(groups):
                group='Segment-'+str(g+1);start=f'2028-01-{10+g if family==1 else 10:02d}T09:00:00+00:00';end=f'2028-01-{15+g if family==1 else 15:02d}T09:00:00+00:00'
                materials=[]
                for a in range(posts_per_group):
                    post='73'+str(secrets.randbelow(10**17)).zfill(17)
                    row={'record_key':secrets.token_hex(5),'platform':'TikTok','account_id':account,'business_no':business,
                        'name':name,'group':group,'level':level,'campaign_budget':str(amount) if level=='campaign' else None,
                        'group_budget':str(amount) if level=='adgroup' and family!=5 else None,'post_id':post,
                        'post_url':'https://www.tiktok.com/@validation/video/'+post,'objective':objective,'mode':mode,
                        'currency':'USD','start':start,'end':end,'tz':'UTC','audience':'Audience-'+str(g+1),
                        'language':'en','identity':identity,'cta':'LEARN_MORE' if objective=='traffic' else None,
                        'url':'https://example.org/'+business if objective=='traffic' else None}
                    materials.append({'post_id':post,'identity_id':identity,'cta':row['cta'] or '', 'landing_url':row['url'] or ''})
                    if family==3:row['account_id']=None
                    if family==4:
                        defaults={'audience':'Shared audience','language':'en'};row['audience']=None;row['language']=None
                    if family=='conflict':
                        row['campaign_budget']=str(amount+g);normal={'budget':str(amount)};clarify='budget'
                    if family=='precision':
                        row['post_id']=int(post);row['post_url']=None;normal={'post_id':post};clarify='post_id'
                    rows.append(row)
                group_amount=(Decimal('33.34') if g<2 else Decimal('33.33')) if family==5 else amount if level=='adgroup' else None
                expected_groups.append({'name':group,'audience':defaults.get('audience','Audience-'+str(g+1)),
                    'language':'en','start_at':start,'end_at':end,'timezone':'UTC','amount':str(group_amount) if group_amount is not None else None,'ads':materials})
            plans.append({'name':name,'account_id':account,'business_no':business,'objective':objective,'budget_level':level,
                'currency':'USD','mode':mode,'total':str(amount if level=='campaign' or family==5 else amount*groups),'groups':expected_groups})
        message=f'本次仅处理业务编号 {business}，按表中明确的计划、广告组和帖子组织完整搭建预览。'
        if family==2:message+=' 表内两个目标分别建立计划。'
        if family==5:message+=' 三个广告组按表顺序以1:1:1分配，总额100.01美元，按分计，余数按表顺序分配。'
        if family=='scope':message='其余的照表搭建。';normal={'scope':f'本次仅新增搭建业务编号 {business}'};clarify='scope'
        fields=list(HEADERS);rng.shuffle(fields);workbook=Workbook();sheet=workbook.active;sheet.title='Batch-'+secrets.token_hex(2)
        sheet.append(['Campaign requirements'])
        for _ in range(rng.randint(0,6)):sheet.append([])
        sheet.append([HEADERS[f] for f in fields])
        for row in rows:sheet.append([row[f] for f in fields])
        if family==3:
            client=workbook.create_sheet('Client');client.append(['Account ID',account])
        # A complete, unrelated business is evidence, never authorized batch selection.
        decoy=dict(rows[0],business_no='OTHER-'+secrets.token_hex(3),name='Unselected-'+secrets.token_hex(3))
        sheet.append([decoy[f] for f in fields])
        directory=inputs/case;directory.mkdir();workbook.save(directory/'brief.xlsx')
        history=Workbook();history.active.title='Ads';history.active.append(['Campaign ID','Ad Group ID','Ad ID','Post ID','Account ID','Timezone']);history.save(directory/'export.xlsx')
        (directory/'paste.txt').write_text(message,encoding='utf-8')
        contexts[case]={'account_id':account,'source':'Generated explicit account metadata',
            'project_context':{'version':0,'defaults':defaults,'account_ids':[account],'objectives':goals,'confirmed_by':'fixture-admin'},'normal_answers':normal}
        oracle[case]={'family':family,'clarification_field':clarify,'plans':plans}
    cases=list(oracle);rng.shuffle(cases)
    (inputs/'protocol.json').write_text(json.dumps({'cases':cases}),encoding='utf-8')
    (inputs/'setup-context.json').write_text(json.dumps(contexts,ensure_ascii=False),encoding='utf-8')
    (root/'oracle.json').write_text(json.dumps(oracle,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'dataset.json').write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat(),'frozen_commit':commit,'complete_cases':60,'clarification_cases':30,'label':'new_synthetic_generalization_not_independent_user_blind_test'}),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--commit',required=True)
    args=parser.parse_args();generate(args.output,args.commit)

