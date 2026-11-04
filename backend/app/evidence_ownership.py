"""Bind file facts to evidenced entities, independently of their cell values."""
import re
from .models import SourceRef


def verify_owner(workbook,document,ref,owner,field):
    from .excel import clean,post_id_from_url,normalize_value,_anchor
    sources=owner.get('sources',{})
    kind=owner.get('_entity_kind') or ('adgroup' if 'group_key' in sources else 'campaign')
    keys=('post_id','post_url','entity_key','group_key','account_id','business_no') if kind=='material' else ('entity_key','group_key','campaign_key','account_id','business_no') if kind=='adgroup' else ('entity_key','name','account_id','business_no')
    anchors=[(key,SourceRef.model_validate(sources[key])) for key in keys if sources.get(key)]
    anchors=[(key,a) for key,a in anchors if a.kind=='excel' and a.file_id==ref.file_id]
    mappings=document.get('mapping') or []
    if isinstance(mappings,dict):mappings=mappings.get('mappings',[])
    fields=['group_key'] if field=='name' and kind=='adgroup' else ['budget','adgroup_budget'] if field=='budget' and kind=='adgroup' else ['budget','campaign_budget'] if field=='budget' else [field]
    if field in ('post_id','platform'):fields.append('post_url')
    def value(cell,key):
        cell=_anchor(workbook[cell.parent.title],cell)
        raw=cell.hyperlink.target if cell.hyperlink and key in ('post_id','post_url') else cell.value
        return (post_id_from_url(raw) or clean(raw)) if key in ('post_id','post_url') else clean(raw)
    def bound(mapping,key,row):
        binding=mapping.get('bindings',{}).get(key)
        if not binding:return None
        sheet=binding.get('sheet') or mapping['sheet']
        coordinate=binding.get('column','')+str(row) if binding['kind']=='row' else binding['cell']
        return value(workbook[sheet][coordinate],key)
    def belongs(mapping,row):
        comparable=0
        stable=False
        for key,anchor in anchors:
            if anchor.sheet!=mapping['sheet']:continue
            if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]*',anchor.cell):raise ValueError('实体身份来源无效')
            original=workbook[anchor.sheet][anchor.cell]
            binding=mapping.get('bindings',{}).get(key,{})
            if key=='entity_key':
                binding=next((b for k,b in mapping.get('bindings',{}).items() if k in ('campaign_key','name') and (b.get('cell')==anchor.cell or b.get('column')==original.column_letter)),{})
            if binding.get('kind')=='cell':other=original
            else:other=workbook[mapping['sheet']].cell(row,original.column)
            if not value(original,key) or value(original,key)!=value(other,key):return False
            comparable+=1
            stable=stable or key in ('entity_key','group_key','campaign_key','post_id','post_url')
        primary='post_id' if kind=='material' else 'group_key' if kind=='adgroup' else 'name'
        expected=owner.get('post_id') if kind=='material' else owner.get('name')
        actual=bound(mapping,primary,row)
        if kind=='material' and actual is None:actual=bound(mapping,'post_url',row)
        if not stable and actual is not None and expected:
            if actual!=str(expected):return False
            comparable+=1
        checks={'account_id':owner.get('account_id'),'business_no':owner.get('business_no'),'objective':owner.get('objective')}
        if kind!='campaign' and not any(k=='entity_key' and a.sheet==mapping['sheet'] for k,a in anchors):checks['name']=owner.get('_campaign_name')
        if kind=='material' and not any(k=='group_key' and a.sheet==mapping['sheet'] for k,a in anchors):checks['group_key']=owner.get('_group_name')
        for key,wanted in checks.items():
            actual=bound(mapping,key,row)
            if actual is None or not wanted:continue
            if key=='objective':actual=normalize_value(key,actual)
            if str(actual)!=str(wanted):return False
        if not anchors and kind=='adgroup' and owner.get('_campaign_name') and bound(mapping,'name',row) is None:return False
        return comparable>0
    source=workbook[ref.sheet][ref.cell]
    for mapping in mappings:
        for key in fields:
            binding=mapping.get('bindings',{}).get(key,{})
            if binding.get('kind')=='row':
                if mapping['sheet']!=ref.sheet or binding.get('column')!=source.column_letter:continue
                rows=[source.row]
                for merged in workbook[ref.sheet].merged_cells.ranges:
                    if source.coordinate in merged:rows=range(merged.min_row,merged.max_row+1);break
                if any(mapping['first_row']<=row<=mapping['last_row'] and belongs(mapping,row) for row in rows):return
            elif binding.get('kind')=='cell' and (binding.get('sheet') or mapping['sheet'])==ref.sheet and binding.get('cell')==ref.cell:
                if any(belongs(mapping,row) for row in range(mapping['first_row'],mapping['last_row']+1)):return
    if not mappings:
        same=[(key,a) for key,a in anchors if a.sheet==ref.sheet]
        if same and belongs({'sheet':ref.sheet,'bindings':{}},source.row):return
        if not anchors and not owner.get('_entity_kind') and not owner.get('id'):return
    raise ValueError('字段来源尚未证明属于当前实体，请确认文件中的实体及字段映射或提供明确修正')
