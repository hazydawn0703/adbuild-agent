import pytest
from app.objective_spelling import spelling_candidate,correction_source
from app.models import Requirement,SourceRef
from app.provenance import supported_source
from app.excel import read_workbook
from io import BytesIO
from openpyxl import Workbook
from hashlib import sha256


@pytest.mark.parametrize('raw,target',[('trafic','traffic'),('trraffic','traffic'),('trffic','traffic'),('video vews','video_views'),('folowers','followers'),('涨份','followers'),('张粉','followers')])
def test_unique_bounded_spelling_is_only_a_proposal(raw,target):
    assert spelling_candidate(raw)==target
    from app.excel import objective
    assert objective(raw) is None


@pytest.mark.parametrize('raw',['reach','安装','涨价','video news','trafficx','video views and traffic','涨份和流量','trefic','花钱','unknown'])
def test_unknown_or_compound_goals_are_never_spelling_candidates(raw):
    assert spelling_candidate(raw) is None


@pytest.mark.parametrize('raw',['nottraffic','not traffic','unfollow','trafficking','涨份和流量','unknown/video_views'])
def test_unknown_negated_and_partly_unknown_compounds_are_not_valid_objectives(raw):
    from app.excel import objective
    assert objective(raw) is None


def test_correction_rereads_owned_cell_and_keeps_interpretation_separate(tmp_path,monkeypatch):
    wb=Workbook();sheet=wb.active;sheet.title='Brief';sheet.append(['Plan','Goal']);sheet.append(['P','trafic']);sheet.append(['Other','video vews'])
    output=BytesIO();wb.save(output);data=output.getvalue();path=tmp_path/'brief.xlsx';path.write_bytes(data)
    monkeypatch.setattr('app.storage.target',lambda _:path)
    source=SourceRef(file_id='file',sheet='Brief',cell='B2',raw_value='MODEL RAW IS NOT FACT')
    name=SourceRef(file_id='file',sheet='Brief',cell='A2',raw_value='P')
    req=Requirement(id='plan',name='P',sources={'objective':source,'name':name,'entity_key':name})
    doc={'id':'file','storage_path':'file','sha256':sha256(data).hexdigest(),'mapping':[{'sheet':'Brief','first_row':2,'last_row':3,'bindings':{'name':{'kind':'row','column':'A'},'objective':{'kind':'row','column':'B'}}}]}
    state={'documents':[doc]};row=req.model_dump(mode='json')
    verified=supported_source(state,row,'objective','traffic',source)
    assert verified.kind=='calculated' and verified.file_id=='' and 'objective_spelling:trafic->traffic' in verified.derived_from
    assert supported_source(state,row,'objective','traffic',verified)==verified
    assert req.objective is None
    with pytest.raises(ValueError):supported_source(state,row,'objective','video_views',source.model_copy(update={'cell':'B3'}))
    assert correction_source(state,row,'followers',source) is None
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='指纹'):correction_source(state,row,'traffic')
