"""Prepare declared clarification facts; never copy latent or final reference plans.

Run separately before freezing execution inputs. The model runner receives only
these permitted user facts, not the reference files or this preparation process.
"""
import argparse
import json
from pathlib import Path
import shutil


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
        facts['structure']='预算层级为 '+answers['level']+'；用户补充事实：'+json.dumps(answers,ensure_ascii=False)
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
    for case,context in setup.items():
        # Only the predeclared answer object is allowed across this boundary.
        permitted=json.loads((Path(references)/(case+'.json')).read_text(encoding='utf-8-sig'))['expected']['permitted_answers']
        context['normal_answers'],context['entity_answers']=permitted_facts(permitted)
    (output/'setup-context.json').write_text(json.dumps(setup,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','references','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();prepare(args.inputs,args.references,args.output)
