"""Audit optional URL expectations against original inputs and declared ID answers.

Runs outside model execution. Preserve originals; write a separate reference set,
an explicit change ledger, and hashes. Never invent a URL from a post ID.
"""
import argparse
import hashlib
import json
from pathlib import Path
from openpyxl import load_workbook


def audit(inputs,references,output):
    inputs=Path(inputs);references=Path(references);output=Path(output)
    output.mkdir();changes=[];original_hashes={};input_hashes={}
    for path in sorted(references.glob('RV*.json')):
        case=path.stem;data=json.loads(path.read_text(encoding='utf-8-sig'))
        original_hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        answers=data['expected']['permitted_answers']
        ids={str(post) for posts in answers.get('post_ids_by_group',{}).values() for post in posts}
        supplied=[]
        for name in ('brief.xlsx','export.xlsx'):
            document=inputs/case/name;input_hashes[case+'/'+name]=hashlib.sha256(document.read_bytes()).hexdigest()
            workbook=load_workbook(document,data_only=False)
            for sheet in workbook:
                for row in sheet:
                    for cell in row:
                        if cell.value is not None:supplied.append(str(cell.value))
                        if cell.hyperlink and cell.hyperlink.target:supplied.append(cell.hyperlink.target)
            workbook.close()
        message=inputs/case/'paste.txt';input_hashes[case+'/paste.txt']=hashlib.sha256(message.read_bytes()).hexdigest()
        supplied.extend([message.read_text(encoding='utf-8-sig'),json.dumps(answers,ensure_ascii=False)])
        final=data['expected']['final_result_for_grading']
        if final:
            for pi,plan in enumerate(final['campaigns']):
                for gi,group in enumerate(plan['groups']):
                    for ai,ad in enumerate(group['ads']):
                        post=ad['post_id'];url=ad.get('post_url')
                        if post not in ids or not url or any('/video/'+post in text for text in supplied):continue
                        changes.append({'case':case,'path':f'expected.final_result_for_grading.campaigns[{pi}].groups[{gi}].ads[{ai}].post_url',
                            'old':url,'new':'','reason':'Exact ID is a declared answer; no source or declared answer supplies this URL. URL is optional for an identified post.'})
                        ad['post_url']=''
        (output/path.name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    ledger={'rule_version':'optional-post-url-source-audit-1','original_reference_hashes':original_hashes,
        'input_hashes':input_hashes,'changes':changes,'limitation':'Only reference inconsistency is corrected; no observed system output is read or used.'}
    (output/'REFERENCE_AUDIT.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
    seal={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    (output/'REFERENCE_SEAL.json').write_text(json.dumps(seal,indent=2),encoding='utf-8')
    return ledger


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','references','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();result=audit(args.inputs,args.references,args.output)
    print(json.dumps({'rule':result['rule_version'],'changes':len(result['changes']),'cases':sorted({c['case'] for c in result['changes']})}))
