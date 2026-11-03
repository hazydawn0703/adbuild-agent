"""Reuse reviewed header semantics, never stale record bounds or data samples."""
from .models import SheetMapping
from openpyxl.utils.cell import coordinate_from_string
from .excel import clean


def header_at(inspection,sheet,row):
    s=next(s for s in inspection['sheets'] if s['sheet']==sheet)
    matches=[h['cells'] for r in s['regions'] for h in r['header_candidates'] if h['row']==row]
    if len(matches)!=1: raise ValueError('无法唯一定位表头，需重新审核')
    return {coordinate_from_string(k)[0]:clean(v) for k,v in matches[0].items()}


def capture(mappings,inspection):
    result=[]
    for m in mappings:
        header=header_at(inspection,m.sheet,m.header_row)
        fields={field:header.get(col,'') for field,col in m.columns.items()}
        if not all(fields.values()) or len(set(fields.values()))!=len(fields):
            raise ValueError('表头为空或重名，不能保存可复用模板')
        result.append({'fields':fields,'defaults':m.defaults})
    return {'regions':result}


def reuse(template,inspection):
    mappings=[]
    used=set()
    for entry in template['regions']:
        candidates=[]
        for sheet in inspection['sheets']:
            for region in sheet['regions']:
                for h in region['header_candidates']:
                    header=header_at(inspection,sheet['sheet'],h['row'])
                    columns={}
                    for field,name in entry['fields'].items():
                        cols=[c for c,v in header.items() if v==name]
                        if len(cols)!=1: break
                        columns[field]=cols[0]
                    else:
                        if region['last_row']>h['row']:
                            candidates.append(SheetMapping(sheet=sheet['sheet'],header_row=h['row'],first_row=h['row']+1,last_row=region['last_row'],columns=columns,defaults=entry['defaults']))
        if len(candidates)!=1: return None
        candidate=candidates[0];key=(candidate.sheet,candidate.header_row)
        if key in used: return None
        used.add(key);mappings.append(candidate)
    return mappings or None
