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


def test_other_groups_real_cell_cannot_silently_replace_existing_schedule(monkeypatch,tmp_path):
    w=Workbook();w.active['A1']='2028-01-15T09:00:00Z';w.active['A2']='2028-01-16T09:00:00Z'
    b=BytesIO();w.save(b);data=b.getvalue();path=tmp_path/'source.xlsx';path.write_bytes(data)
    monkeypatch.setattr('app.storage.target',lambda _:path)
    state={'documents':[{'id':'owned','storage_path':'private','sha256':sha256(data).hexdigest()}]}
    old=SourceRef(file_id='owned',sheet='Sheet',cell='A1',raw_value='2028-01-15T09:00:00Z')
    other=SourceRef(file_id='owned',sheet='Sheet',cell='A2',raw_value='untrusted')
    row={'id':'g1','end_at':'2028-01-15T09:00:00Z','sources':{'end_at':old.model_dump()}}
    with pytest.raises(ValueError,match='冲突'):supported_source(state,row,'end_at','2028-01-16T09:00:00Z',other)
    row.update(end_at=None,issues=[{'field':'end_at','code':'invalid_structure'}])
    with pytest.raises(ValueError,match='冲突'):supported_source(state,row,'end_at','2028-01-16T09:00:00Z',other)


def test_url_derived_post_and_platform_reverify_the_original_cell(monkeypatch,tmp_path):
    w=Workbook();w.active['A1']='https://www.tiktok.com/@fixture/video/7312345678901234567';b=BytesIO();w.save(b)
    data=b.getvalue();path=tmp_path/'post.xlsx';path.write_bytes(data);monkeypatch.setattr('app.storage.target',lambda _:path)
    state={'documents':[{'id':'owned','storage_path':'private','sha256':sha256(data).hexdigest()}]}
    ref=SourceRef(file_id='owned',sheet='Sheet',cell='A1',raw_value='untrusted')
    assert supported_source(state,{},'post_id','7312345678901234567',ref).raw_value.endswith('7312345678901234567')
    assert supported_source(state,{},'platform','TikTok',ref).kind=='excel'
    with pytest.raises(ValueError):supported_source(state,{},'post_id','OTHER',ref)
