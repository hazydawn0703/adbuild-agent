"""Bounded spelling proposals for this product's known objective vocabulary."""
from difflib import SequenceMatcher
import re
from .excel import objective,clean

LABELS={'followers':('followers','follow','涨粉','买粉'),
        'video_views':('video views','video view','视频观看','视频播放'),
        'traffic':('traffic','流量','点击')}


def spelling_candidate(raw):
    text=re.sub(r'[_\s-]+',' ',clean(raw).casefold())
    if not text or objective(text) is not None:return None
    found=set()
    for target,labels in LABELS.items():
        for label in labels:
            if re.fullmatch(r'[a-z ]+',text) and re.fullmatch(r'[a-z ]+',label) and min(len(text),len(label))>=5:
                changes=[op for op in SequenceMatcher(None,text,label,autojunk=False).get_opcodes() if op[0]!='equal']
                if len(changes)!=1:continue
                kind,a,b,c,d=changes[0]
                missing=label[c:d] if kind=='insert' else text[a:b] if kind=='delete' else ''
                original=label if kind=='insert' else text;index=c if kind=='insert' else a
                if len(missing)==1 and (missing in 'aeiou' or index>0 and original[index-1]==missing or index+1<len(original) and original[index+1]==missing):found.add(target)
            elif re.fullmatch(r'[\u4e00-\u9fff]{2,8}',text) and len(text)==len(label) and sum(a!=b for a,b in zip(text,label))==1:
                from pypinyin import lazy_pinyin
                if lazy_pinyin(text)==lazy_pinyin(label):found.add(target)
    return next(iter(found)) if len(found)==1 else None


def correction_source(state,row,value,offered=None):
    if row.get('objective') not in (None,''):return None
    original=row.get('sources',{}).get('objective')
    if not original:return None
    from .models import SourceRef
    original=SourceRef.model_validate(original)
    if original.kind!='excel':return None
    document=next((d for d in state.get('documents',[]) if str(d['id'])==original.file_id),None)
    if document is None or not document.get('storage_path') or not document.get('sha256'):return None
    from .provenance import read_verified_workbook
    from .evidence_ownership import verify_owner
    from .excel import _anchor,sensitive_cells
    workbook=read_verified_workbook(document)
    if original.sheet not in workbook.sheetnames or not re.fullmatch('[A-Z]{1,3}[1-9][0-9]*',original.cell):return None
    sheet=workbook[original.sheet];cell=_anchor(sheet,sheet[original.cell])
    if cell.coordinate in sensitive_cells(sheet):return None
    raw=clean(cell.value);candidate=spelling_candidate(raw)
    if candidate is None or objective(value)!=candidate:return None
    verify_owner(workbook,document,original,row,'objective')
    origins={f'excel:{original.file_id}:{original.sheet}!{cell.coordinate}',f'objective_spelling:{raw}->{candidate}'}
    mappings=document.get('mapping') or []
    if isinstance(mappings,dict):mappings=mappings.get('mappings',[])
    for mapping in mappings:
        binding=mapping.get('bindings',{}).get('objective')
        if not binding:continue
        bound_sheet=workbook[binding.get('sheet') or mapping['sheet']]
        positions=[binding['cell']] if binding['kind']=='cell' else [binding['column']+str(i) for i in range(mapping['first_row'],mapping['last_row']+1)]
        for position in positions:
            item=_anchor(bound_sheet,bound_sheet[position]);text=clean(item.value)
            if not text:continue
            ref=original.model_copy(update={'sheet':bound_sheet.title,'cell':item.coordinate})
            try:verify_owner(workbook,document,ref,row,'objective')
            except ValueError:continue
            if (objective(text) or spelling_candidate(text))!=candidate:return None
            origins.add(f'excel:{original.file_id}:{bound_sheet.title}!{item.coordinate}')
            if objective(text) is None:origins.add(f'objective_spelling:{text}->{candidate}')
    source=SourceRef(kind='calculated',file_id='',sheet='',cell='',raw_value=candidate,
        derived_from=sorted(origins))
    if offered:
        ref=SourceRef.model_validate(offered)
        if ref.kind=='excel':
            if (ref.file_id,ref.sheet,ref.cell)!=(original.file_id,original.sheet,cell.coordinate):return None
        elif ref.kind=='calculated':
            if ref.file_id or set(ref.derived_from)!=set(source.derived_from):return None
        else:return None
    return source


def correction_suggestions(state):
    result=[]
    for row in (state.get('draft') or {}).get('requirements',[]):
        if not row.get('included',True) or row.get('objective'):continue
        original=row.get('sources',{}).get('objective') or {}
        candidate=spelling_candidate(original.get('raw_value',''))
        if candidate is None:continue
        source=correction_source(state,row,candidate)
        if source:result.append({'requirement_id':row['id'],'field':'objective','value':candidate,'source':source.model_dump(mode='json'),'original_value':original['raw_value']})
    return result
