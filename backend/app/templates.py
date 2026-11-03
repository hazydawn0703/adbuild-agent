"""Reuse reviewed header semantics, never stale record bounds or data samples."""
from .models import SheetMapping
from openpyxl.utils.cell import coordinate_from_string
from .excel import clean,read_workbook


def capture(mappings,inspection,data):
    workbook=read_workbook(data)
    result=[]
    for m in mappings:
        sheet=workbook[m.sheet]
        header={coordinate_from_string(c.coordinate)[0]:clean(c.value) for c in sheet[m.header_row] if c.value is not None}
        fields={field:header.get(col,'') for field,col in m.columns.items()}
        if not all(fields.values()) or len(set(fields.values()))!=len(fields):
            raise ValueError('表头为空或重名，不能保存可复用模板')
        result.append({'fields':fields,'defaults':{}})
    return {'regions':result,'layout':sorted(len(s['regions']) for s in inspection['sheets'])}


def reuse(template,inspection,data):
    if template['layout']!=sorted(len(s['regions']) for s in inspection['sheets']): return None
    workbook=read_workbook(data)
    mappings=[]
    used=set()
    for entry in template['regions']:
        candidates=[]
        for sheet in inspection['sheets']:
            for region in sheet['regions']:
                actual_rows={}
                for cell in workbook[sheet['sheet']]._cells.values():
                    if region['first_row']<=cell.row<=region['last_row'] and cell.value is not None:
                        actual_rows.setdefault(cell.row,{})[coordinate_from_string(cell.coordinate)[0]]=clean(cell.value)
                for row_index,header in actual_rows.items():
                    columns={}
                    for field,name in entry['fields'].items():
                        cols=[c for c,v in header.items() if v==name]
                        if len(cols)!=1: break
                        columns[field]=cols[0]
                    else:
                        if region['last_row']>row_index:
                            candidates.append(SheetMapping(sheet=sheet['sheet'],header_row=row_index,first_row=row_index+1,last_row=region['last_row'],columns=columns,defaults=entry['defaults']))
        if len(candidates)!=1: return None
        candidate=candidates[0];key=(candidate.sheet,candidate.header_row)
        if key in used: return None
        used.add(key);mappings.append(candidate)
    return mappings or None
