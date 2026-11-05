"""Audit question field labels from original inputs, never from observed responses."""
import re
from decimal import Decimal
from app.excel import read_workbook,_anchor,clean,normalize_value
from app.provenance import _budget_values


def budget_owner_question(inputs,expected):
    answers=expected.get('permitted_answers',{})
    if 'budget' not in expected.get('required_initial_questions',[]) or answers.get('level') not in ('campaign','adgroup'):return None
    # Format or conflicting-amount questions cannot be replaced by ownership.
    if answers.get('decimal_separator') is not None:return None
    text=(inputs/'paste.txt').read_text(encoding='utf-8-sig')
    clause=re.search(r'(?:预算|总额|金额)([^。；;\n]*)',text)
    if not clause or len(re.findall(r'[-+]?\d+(?:[.,]\d+)*',clause[1]))!=1:return None
    if re.search(r'或|还是|大约|左右|至少|最多|[~～]',clause[1]):return None
    values=set(_budget_values(text))
    if len(values)!=1:return None
    value=next(iter(values))
    if value<=0:return None
    scopes=re.findall(r'(?<![0-9A-Za-z_])BR-[0-9A-Za-z_-]+',text)
    if len(set(scopes))!=1:return None
    workbook=read_workbook((inputs/'brief.xlsx').read_bytes());groups=set();rows=[]
    labels={'business':{'业务编号','businessno'},'group':{'广告组','广告组名称','adgroup','adgroupname'},
            'level':{'预算层级','budgetlevel'},'campaign':{'计划总额','计划预算','campaignbudget'},'adgroup':{'广告组预算','adgroupbudget'},'generic':{'预算金额','预算','budget'}}
    for sheet in workbook:
        headers={}
        for row in sheet:
            normalized={re.sub(r'[\W_]+','',clean(c.value)).casefold():c.column for c in row if c.value is not None}
            matched={field:next((normalized[name] for name in names if name in normalized),None) for field,names in labels.items()}
            if matched['business'] and matched['group'] and matched['level'] and matched['campaign'] and matched['adgroup']:
                headers=matched;first=row[0].row+1;break
        if not headers:continue
        for index in range(first,sheet.max_row+1):
            at=lambda field:_anchor(sheet,sheet.cell(index,headers[field])).value if headers.get(field) else None
            if clean(at('business'))!=scopes[0]:continue
            group=clean(at('group'))
            if not group:continue
            if any(at(field) not in (None,'') for field in ('level','campaign','adgroup','generic')):return None
            groups.add(group);rows.append(f'{sheet.title}!{index}')
    if len(groups)<2:return None
    total=answers.get('budget_total',answers.get('total'))
    if total is None:return None
    try:
        total=normalize_value('budget',total)
        if answers['level']=='campaign':
            if total!=value:return None
        elif normalize_value('budget',answers.get('per_group'))!=value or total!=value*len(groups):return None
    except (ValueError,TypeError):return None
    return {'known_amount':str(value),'group_count':len(groups),'source_rows':rows,'declared_level':answers['level'],
            'reason':'Input provides one amount but no ownership. Ask budget_level first; per-group amount questions may follow that answer. No additional amount is required for campaign ownership.'}
