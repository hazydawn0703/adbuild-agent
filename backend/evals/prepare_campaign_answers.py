"""Prepare declared clarification facts; never copy latent or final reference plans.

Run separately before freezing execution inputs. The model runner receives only
these permitted user facts, not the reference files or this preparation process.
"""
import argparse
import json
from pathlib import Path
import shutil
import re


def correct_account_setup(context,workbook_data):
    """A default's applicability list is not the test project's advertiser binding."""
    if not context.get('source','').endswith('applicable_account_ids[0]'):return None
    from app.excel import read_workbook,clean
    headers={'accountid','advertiserid','广告账户','广告账户id','广告账号','广告账号id'}
    accounts=set();refs=[]
    for sheet in read_workbook(workbook_data):
        for cell in list(sheet._cells.values()):
            if re.sub(r'[\W_]+','',clean(cell.value)).casefold() not in headers:continue
            for row in range(cell.row+1,sheet.max_row+1):
                item=sheet.cell(row,cell.column)
                if isinstance(item.value,str) and re.fullmatch(r'\d{8,30}',item.value.strip()):
                    accounts.add(item.value.strip());refs.append(f'{sheet.title}!{item.coordinate}')
    if len(accounts)!=1:return None
    account=next(iter(accounts))
    if context.get('account_id')==account:return None
    previous={'account_id':context.get('account_id'),'source':context['source']}
    context.update(account_id=account,source='Test project setup from the single explicit advertiser in requirement cells: '+', '.join(refs))
    return {'previous':previous,'account_id':account,'cells':refs,'rule':'default_applicability_is_not_project_binding'}


def permitted_facts(answers):
    facts={};entities=[]
    direct={'objective','timezone','identity_id','start_at','account_id','currency'}
    for field in direct & answers.keys():facts[field]=str(answers[field])
    for field in ('budget_total','total'):
        if field in answers:facts['budget']=str(answers[field])
    if 'business_nos' in answers:facts['scope']='本次仅处理业务编号 '+ '、'.join(answers['business_nos'])
    if 'batch_name' in answers:
        facts['included']='仅采纳历史批次 '+answers['batch_name']+' 的字段 '+ '、'.join(answers.get('adopt_fields',[]))
        facts['scope']=facts['included']
    for field in ('start_time','end_time'):
        if field in answers:facts[{'start_time':'start_at_time','end_time':'end_at_time'}[field]]=answers[field]
    if 'level' in answers:
        facts['budget_level']=answers['level']
    for name,target in (('audiences','audience'),('group_allocation','budget')):
        for group,value in (answers.get(name) or {}).items():
            if value is not None:entities.append({'field':target,'value':str(value),'group_index':int(group.removeprefix('G'))})
    for name in ('post_ids_by_group','posts_by_group'):
        for group,posts in (answers.get(name) or {}).items():
            for index,post in enumerate(posts,1):
                for field,value in ({'post_id':post} if isinstance(post,str) else post).items():
                    entities.append({'field':field,'value':value,'group_index':int(group.removeprefix('G')),'material_index':index})
    if answers.get('per_group') is not None:
        entities.append({'field':'budget','value':str(answers['per_group']),'all_groups':True})
    return facts,entities


def prepare(inputs,references,output):
    output=Path(output);shutil.copytree(inputs,output)
    setup=json.loads((output/'setup-context.json').read_text(encoding='utf-8-sig'))
    account_changes={}
    for case,context in setup.items():
        change=correct_account_setup(context,(output/case/'brief.xlsx').read_bytes())
        if change:account_changes[case]=change
        # Only the predeclared answer object is allowed across this boundary.
        permitted=json.loads((Path(references)/(case+'.json')).read_text(encoding='utf-8-sig'))['expected']['permitted_answers']
        context['normal_answers'],context['entity_answers']=permitted_facts(permitted)
    (output/'setup-context.json').write_text(json.dumps(setup,ensure_ascii=False,indent=2),encoding='utf-8')
    with output.with_name(output.name+'-account-setup-audit.json').open('x',encoding='utf-8') as stream:json.dump(account_changes,stream,ensure_ascii=False,indent=2)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','references','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();prepare(args.inputs,args.references,args.output)
