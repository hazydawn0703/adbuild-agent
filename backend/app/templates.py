"""Reuse reviewed source relationships; record selection is recalculated per task."""
from .models import SheetMapping,Binding
from openpyxl.utils.cell import coordinate_from_string
from .excel import clean,read_workbook,validate_mapping


def _label(sheet,cell):
    for dr,dc,direction in ((0,-1,'right'),(-1,0,'below')):
        if cell.row+dr<1 or cell.column+dc<1:continue
        value=clean(sheet.cell(cell.row+dr,cell.column+dc).value)
        if value and not value.isdigit() and not value.startswith(('http://','https://')):
            return {'label':value,'direction':direction}
    raise ValueError('固定来源缺少可重定位的标签，需要重新审核')


def capture(mappings,inspection,data):
    workbook=read_workbook(data);result=[]
    for m in mappings:
        validate_mapping(m,workbook)
        # Batch predicates may contain a business number or selected post. They are
        # deliberately not promoted into a project's durable mapping template.
        if m.filters:raise ValueError('包含本批筛选的映射需重新审核范围，不能直接保存为长期模板')
        sheet=workbook[m.sheet]
        header={coordinate_from_string(c.coordinate)[0]:clean(c.value) for c in sheet[m.header_row] if c.value is not None}
        fields={field:{'header':header.get(col,''),'transform':'value'} for field,col in m.columns.items()}
        fixed={}
        for field,binding in m.bindings.items():
            source_sheet=workbook[binding.sheet or m.sheet]
            if binding.kind=='row':fields[field]={'header':header.get(binding.column,''),'transform':binding.transform}
            elif binding.kind=='header':fixed[field]={'label':clean(source_sheet[binding.cell].value),'direction':'self','kind':'header','transform':binding.transform}
            else:fixed[field]={**_label(source_sheet,source_sheet[binding.cell]),'kind':'cell','transform':binding.transform}
        labels=[entry['header'] for entry in fields.values()]
        if not fields or not all(labels) or any(list(header.values()).count(label)!=1 for label in labels):raise ValueError('表头为空或重名，不能保存可复用模板')
        result.append({'fields':fields,'fixed':fixed})
    return {'regions':result}


def reuse(template,inspection,data):
    workbook=read_workbook(data);mappings=[];used=set()
    # A changed contract requires a fresh review; no legacy template conversion.
    if not template.get('regions') or any(not isinstance(value,dict) for entry in template['regions'] for value in entry.get('fields',{}).values()):return None
    for entry in template['regions']:
        candidates=[]
        for sheet in inspection['sheets']:
            for region in sheet['regions']:
                actual_rows={}
                for cell in workbook[sheet['sheet']]._cells.values():
                    if region['first_row']<=cell.row<=region['last_row'] and cell.value is not None:
                        actual_rows.setdefault(cell.row,{})[coordinate_from_string(cell.coordinate)[0]]=clean(cell.value)
                for row_index,header in actual_rows.items():
                    columns={};bindings={}
                    for field,source in entry['fields'].items():
                        cols=[c for c,value in header.items() if value==source['header']]
                        if len(cols)!=1:break
                        if source['transform']=='value':columns[field]=cols[0]
                        else:bindings[field]=Binding(kind='row',column=cols[0],transform=source['transform'])
                    else:
                        for field,source in entry['fixed'].items():
                            matches=[]
                            for source_sheet in workbook:
                                for cell in list(source_sheet._cells.values()):
                                    if clean(cell.value)!=source['label']:continue
                                    target=cell if source['direction']=='self' else source_sheet.cell(cell.row+(source['direction']=='below'),cell.column+(source['direction']=='right'))
                                    matches.append(Binding(kind=source['kind'],sheet=source_sheet.title,cell=target.coordinate,transform=source['transform']))
                            if len(matches)!=1:break
                            bindings[field]=matches[0]
                        else:
                            if region['last_row']>row_index:candidates.append(SheetMapping(sheet=sheet['sheet'],header_row=row_index,first_row=row_index+1,last_row=region['last_row'],columns=columns,bindings=bindings))
        if len(candidates)!=1:return None
        candidate=candidates[0];key=(candidate.sheet,candidate.header_row)
        if key in used:return None
        try:validate_mapping(candidate,workbook)
        except ValueError:return None
        used.add(key);mappings.append(candidate)
    return mappings or None
