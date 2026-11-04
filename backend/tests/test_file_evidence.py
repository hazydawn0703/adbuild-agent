from hashlib import sha256
from io import BytesIO
import pytest
from openpyxl import Workbook
from app.models import SourceRef
from app.provenance import supported_source


def test_new_excel_source_is_reread_and_not_trusted_from_model(monkeypatch,tmp_path):
    w=Workbook();w.active['A1']='125.50';b=BytesIO();w.save(b);data=b.getvalue()
    path=tmp_path/'source.xlsx';path.write_bytes(data)
    monkeypatch.setattr('app.storage.target',lambda _:path)
    doc={'id':'owned','storage_path':'private','sha256':sha256(data).hexdigest()}
    state={'documents':[doc]}
    ref=SourceRef(file_id='owned',sheet='Sheet',cell='A1',raw_value='MODEL INVENTED RAW')
    actual=supported_source(state,{},'budget','125.50',ref)
    assert actual.raw_value=='125.50'
    with pytest.raises(ValueError,match='不一致'):supported_source(state,{},'budget','500',ref)
    with pytest.raises(ValueError):supported_source({'documents':[]},{},'budget','125.50',ref)
    path.write_bytes(data+b'changed')
    with pytest.raises(ValueError,match='指纹'):supported_source(state,{},'budget','125.50',ref)


def test_project_binding_is_independent_from_workbook_evidence():
    ref=SourceRef(kind='project',file_id='',sheet='',cell='',raw_value='forged')
    actual=supported_source({'project_account_id':'123'},{},'account_id','123',ref)
    assert actual.raw_value=='123' and actual.kind=='project'
    with pytest.raises(ValueError):supported_source({'project_account_id':'456'},{},'account_id','123',ref)


def test_real_cell_from_other_group_cannot_fill_a_missing_field(monkeypatch,tmp_path):
    w=Workbook();s=w.active;s.append(['group','end']);s.append(['G1',None]);s.append(['G2','2028-01-16T09:00:00Z'])
    out=BytesIO();w.save(out);data=out.getvalue();path=tmp_path/'groups.xlsx';path.write_bytes(data)
    monkeypatch.setattr('app.storage.target',lambda _:path)
    state={'documents':[{'id':'owned','storage_path':'private','sha256':sha256(data).hexdigest()}]}
    anchor=SourceRef(file_id='owned',sheet='Sheet',cell='A2',raw_value='G1')
    row={'id':'g1','name':'G1','end_at':None,'sources':{'group_key':anchor.model_dump()}}
    other=SourceRef(file_id='owned',sheet='Sheet',cell='B3',raw_value='not trusted')
    with pytest.raises(ValueError,match='实体'):supported_source(state,row,'end_at','2028-01-16T09:00:00Z',other)


def test_another_record_of_same_group_is_valid_evidence(monkeypatch,tmp_path):
    w=Workbook();s=w.active;s.append(['group','end']);s.append(['G1',None]);s.append(['G1','2028-01-16T09:00:00Z'])
    out=BytesIO();w.save(out);data=out.getvalue();path=tmp_path/'same-group.xlsx';path.write_bytes(data)
    monkeypatch.setattr('app.storage.target',lambda _:path)
    state={'documents':[{'id':'f','storage_path':'private','sha256':sha256(data).hexdigest()}]}
    row={'id':'g','name':'G1','end_at':None,'sources':{'group_key':SourceRef(file_id='f',sheet='Sheet',cell='A2',raw_value='untrusted').model_dump()}}
    actual=supported_source(state,row,'end_at','2028-01-16T09:00:00Z',SourceRef(file_id='f',sheet='Sheet',cell='B3',raw_value='untrusted'))
    assert actual.cell=='B3'


def test_new_file_needs_matching_group_campaign_and_account(monkeypatch,tmp_path):
    w=Workbook();s=w.active;s.append(['plan','group','end','account'])
    for account in ['123','999']:s.append(['Launch','G1','2028-01-16T09:00:00Z',account])
    out=BytesIO();w.save(out);data=out.getvalue();path=tmp_path/'new-file.xlsx';path.write_bytes(data)
    monkeypatch.setattr('app.storage.target',lambda _:path)
    mapping={'sheet':'Sheet','first_row':2,'last_row':3,'bindings':{f:{'kind':'row','column':c} for f,c in [('name','A'),('group_key','B'),('end_at','C'),('account_id','D')]}}
    doc={'id':'new','storage_path':'private','sha256':sha256(data).hexdigest(),'mapping':[mapping]}
    row={'id':'g','name':'G1','account_id':'123','_entity_kind':'adgroup','_campaign_name':'Launch','sources':{}}
    good=SourceRef(file_id='new',sheet='Sheet',cell='C2',raw_value='untrusted')
    assert supported_source({'documents':[doc]},row,'end_at','2028-01-16T09:00:00Z',good).cell=='C2'
    with pytest.raises(ValueError,match='实体'):supported_source({'documents':[doc]},row,'end_at','2028-01-16T09:00:00Z',good.model_copy(update={'cell':'C3'}))


def test_renaming_does_not_break_fixed_campaign_identity_evidence(monkeypatch,tmp_path):
    w=Workbook();s=w.active;s.append(['Launch','group','end']);s.append(['unrelated','G1','2028-01-16T09:00:00Z'])
    out=BytesIO();w.save(out);data=out.getvalue();path=tmp_path/'fixed.xlsx';path.write_bytes(data)
    monkeypatch.setattr('app.storage.target',lambda _:path)
    mapping={'sheet':'Sheet','first_row':2,'last_row':2,'bindings':{'name':{'kind':'cell','cell':'A1'},'group_key':{'kind':'row','column':'B'},'end_at':{'kind':'row','column':'C'}}}
    refs={key:SourceRef(file_id='f',sheet='Sheet',cell=cell,raw_value='untrusted').model_dump() for key,cell in [('entity_key','A1'),('group_key','B2')]}
    row={'id':'g','_entity_kind':'adgroup','name':'Renamed group','_campaign_name':'Renamed campaign','sources':refs}
    doc={'id':'f','storage_path':'private','sha256':sha256(data).hexdigest(),'mapping':[mapping]}
    ref=SourceRef(file_id='f',sheet='Sheet',cell='C2',raw_value='untrusted')
    assert supported_source({'documents':[doc]},row,'end_at','2028-01-16T09:00:00Z',ref).cell=='C2'


def test_other_groups_real_cell_cannot_silently_replace_existing_schedule(monkeypatch,tmp_path):
    w=Workbook();w.active['A1']='2028-01-15T09:00:00Z';w.active['A2']='2028-01-16T09:00:00Z'
    b=BytesIO();w.save(b);data=b.getvalue();path=tmp_path/'source.xlsx';path.write_bytes(data)
    monkeypatch.setattr('app.storage.target',lambda _:path)
    state={'documents':[{'id':'owned','storage_path':'private','sha256':sha256(data).hexdigest()}]}
    old=SourceRef(file_id='owned',sheet='Sheet',cell='A1',raw_value='2028-01-15T09:00:00Z')
    other=SourceRef(file_id='owned',sheet='Sheet',cell='A2',raw_value='untrusted')
    row={'id':'g1','end_at':'2028-01-15T09:00:00Z','sources':{'end_at':old.model_dump()}}
    with pytest.raises(ValueError,match='冲突|实体'):supported_source(state,row,'end_at','2028-01-16T09:00:00Z',other)
    row.update(end_at=None,issues=[{'field':'end_at','code':'invalid_structure'}])
    with pytest.raises(ValueError,match='冲突|实体'):supported_source(state,row,'end_at','2028-01-16T09:00:00Z',other)


def test_url_derived_post_and_platform_reverify_the_original_cell(monkeypatch,tmp_path):
    w=Workbook();w.active['A1']='https://www.tiktok.com/@fixture/video/7312345678901234567';b=BytesIO();w.save(b)
    data=b.getvalue();path=tmp_path/'post.xlsx';path.write_bytes(data);monkeypatch.setattr('app.storage.target',lambda _:path)
    state={'documents':[{'id':'owned','storage_path':'private','sha256':sha256(data).hexdigest()}]}
    ref=SourceRef(file_id='owned',sheet='Sheet',cell='A1',raw_value='untrusted')
    assert supported_source(state,{},'post_id','7312345678901234567',ref).raw_value.endswith('7312345678901234567')
    assert supported_source(state,{},'platform','TikTok',ref).kind=='excel'
    with pytest.raises(ValueError):supported_source(state,{},'post_id','OTHER',ref)
