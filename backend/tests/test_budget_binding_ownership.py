from io import BytesIO
from openpyxl import Workbook
import pytest
from app.excel import parse_requirements,validate_mapping,read_workbook
from app.models import Binding,SheetMapping
from model_response_fixture import row_bindings


def test_generic_budget_is_assigned_once_to_its_explicit_owner():
    workbook=Workbook();sheet=workbook.active
    sheet.append(['Plan','Group','Post','Level','Amount'])
    for group,post in [('G1','100'),('G1','101'),('G2','102')]:sheet.append(['Launch',group,post,'adgroup','300'])
    out=BytesIO();workbook.save(out)
    mapping=SheetMapping(sheet=sheet.title,header_row=1,first_row=2,last_row=4,
        bindings=row_bindings({'name':'A','group_key':'B','post_id':'C','budget_level':'D','budget':'E'}))
    rows=parse_requirements(out.getvalue(),'f',[mapping])
    assert len(rows)==1 and rows[0].budget==600
    assert [g.budget.amount for g in rows[0].structure.groups]==[300,300]


def test_duplicate_flat_and_owned_budget_mapping_is_diagnosable():
    workbook=Workbook();workbook.active.append(['Budget','Group budget']);workbook.active.append([100,100])
    mapping=SheetMapping(sheet=workbook.active.title,header_row=1,first_row=2,last_row=2,
        bindings=row_bindings({'budget':'A','adgroup_budget':'B'}))
    with pytest.raises(ValueError,match='bindings.budget'):validate_mapping(mapping,workbook)


def test_material_record_key_cannot_be_used_to_split_campaigns():
    workbook=Workbook();sheet=workbook.active
    sheet.append(['记录键','计划名称','广告组']);sheet.append(['R1','Launch','G1']);sheet.append(['R2','Launch','G2'])
    mapping=SheetMapping(sheet=sheet.title,header_row=1,first_row=2,last_row=3,
        bindings=row_bindings({'campaign_key':'A','name':'B','group_key':'C'}))
    with pytest.raises(ValueError,match='campaign_key'):validate_mapping(mapping,workbook)
    mapping.bindings['record_key']=mapping.bindings['campaign_key']
    with pytest.raises(ValueError,match='campaign_key'):validate_mapping(mapping,workbook)


def test_answered_owner_recovers_amounts_only_from_unchanged_owned_file(tmp_path,monkeypatch):
    from hashlib import sha256
    from app.entities import set_budget_level,recover_group_budgets
    workbook=Workbook();sheet=workbook.active
    sheet.append(['Plan','Group','Post','Amount'])
    sheet.append(['Launch','G1','100','300']);sheet.append(['Launch','G1','101','300'])
    out=BytesIO();workbook.save(out);data=out.getvalue();path=tmp_path/'budget.xlsx';path.write_bytes(data)
    monkeypatch.setattr('app.storage.target',lambda _:path)
    mapping=SheetMapping(sheet=sheet.title,header_row=1,first_row=2,last_row=3,
        bindings=row_bindings({'name':'A','group_key':'B','post_id':'C','budget':'D'}))
    req=parse_requirements(data,'f',[mapping])[0]
    req=set_budget_level(req,'adgroup','m','u')
    docs=[{'id':'f','storage_path':'private','sha256':sha256(data).hexdigest()}]
    actual=recover_group_budgets(req.model_copy(deep=True),docs)
    assert actual.budget==300 and actual.structure.groups[0].budget.amount==300
    path.write_bytes(data+b'changed')
    rejected=recover_group_budgets(req,docs)
    assert rejected.structure.groups[0].budget.amount is None
    assert any(i.code=='invalid_budget_source' for i in rejected.issues)
