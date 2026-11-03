from model_response_fixture import row_bindings
import pytest
from model_response_fixture import model_response
import asyncio,json
from io import BytesIO
from openpyxl import Workbook
from app.agent import propose_mapping
from app.excel import inspect_workbook,parse_requirements
from app.models import MappingProposal,SheetMapping,Binding,RangeRequest,Requirement
from app.scope import matches


def binary(w):
    out=BytesIO();w.save(out);return out.getvalue()


def test_mapping_source_errors_are_repaired_inside_bounded_model_call(monkeypatch):
    w=Workbook();s=w.active;s.append(['Post','Budget']);s.append(['7712345678901234567',40]);data=binary(w)
    calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            bad=Binding(kind='header',column='B') if len(calls)==1 else Binding(kind='header',cell='B1')
            return model_response(MappingProposal(mappings=[SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=2,bindings={**row_bindings({'post_id':'A'}), **{'currency':bad}})],explanation='sources'))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    result=asyncio.run(propose_mapping(inspect_workbook(data),data))
    assert len(calls)==2 and result.complete
    assert result.mappings[0].bindings['currency'].cell=='B1'
    assert '固定来源' in calls[1][-1][1]


def test_removed_parallel_column_mapping_is_rejected():
    with pytest.raises(ValueError):
        SheetMapping.model_validate({'sheet':'Sheet','header_row':1,'first_row':2,'last_row':2,
            'columns':{'account_id':'B'},'bindings':{'account_id':{'kind':'cell','cell':'B2'}}})


def test_pending_range_resumes_saved_cursor_and_retains_all_page_evidence(monkeypatch):
    w=Workbook();s=w.active
    for row in range(1,3002):s.cell(row,1,str(row))
    data=binary(w);inspection=inspect_workbook(data);requests=[];calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(1)
            # First invocation reaches its two-page budget. Resume consumes a
            # saved page before this model is called, then finishes the range.
            needed=len(calls)<5
            return model_response(MappingProposal(mappings=[],requested_ranges=[RangeRequest(sheet=s.title,range='A1:A3001',reason='read source')] if needed else [],explanation='read progress'))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    first=asyncio.run(propose_mapping(inspection,data))
    assert not first.complete and first.pending_ranges
    assert first.read_state['pages'][s.title+'!A1:A3001']['next_cursor']==2000
    task={'analysis':{'mapping':{'proposal':first.model_dump(mode='json')}}}
    final=asyncio.run(propose_mapping(inspection,data,task=task))
    assert final.complete and not final.pending_ranges
    assert [page['coverage']['offset'] for page in final.read_state['evidence']]==[0,1000,2000,3000]
    assert sum(page['coverage']['read_cells'] for page in final.read_state['evidence'])==3001
    assert len(calls)==5


def test_repeated_completed_read_does_not_run_without_progress(monkeypatch):
    w=Workbook();s=w.active;s.append(['a']);data=binary(w);calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(1)
            return model_response(MappingProposal(mappings=[],requested_ranges=[RangeRequest(sheet=s.title,range='A1:A1',reason='same request')],explanation='read'))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    result=asyncio.run(propose_mapping(inspect_workbook(data),data))
    assert not result.complete and '没有新的补读进展' in result.explanation and len(calls)==2
    assert len(result.read_state['evidence'])==1


def test_server_search_finds_deep_target_and_nearby_headers_without_leaking_codes(monkeypatch):
    w=Workbook();s=w.active;s.title='Any name'
    s.cell(32,1,'Business');s.cell(32,2,'Post')
    s.cell(401,1,'batch-target');s.cell(401,2,'https://www.tiktok.com/@demo/video/7712345678901234567')
    c=w.create_sheet('Materials');c.append(['Spark Code','private-permission-value'])
    data=binary(w);messages_seen=[]
    class Model:
        async def ainvoke(self,messages):
            messages_seen.extend(messages)
            return model_response(MappingProposal(mappings=[],explanation='needs review'))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    result=asyncio.run(propose_mapping(inspect_workbook(data),data,task={'scope':{'selectors':[{'field':'post_id','values':['7712345678901234567']}]}}))
    search=result.read_state['search']
    assert search['rows'][0]['row']==401
    assert any(row['row']==32 for row in search['context_rows'])
    assert 'private-permission-value' not in json.dumps(messages_seen)
    assert 'private-permission-value' not in json.dumps(result.read_state)


def test_platform_field_or_reviewed_filter_keeps_its_real_source():
    w=Workbook();s=w.active;s.append(['Business','Platform']);s.append(['batch-target','TikTok']);data=binary(w)
    from app.models import RowFilter
    mapping=SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=2,bindings=row_bindings({'business_no':'A'}),filters=[RowFilter(column='B',operator='eq',values=['TikTok'])])
    row=parse_requirements(data,'file',[mapping])[0]
    assert row.platform=='TikTok' and row.sources['platform'].cell=='B2'
    assert matches(row,{'field':'platform','values':['TikTok']})
    assert matches(row,{'field':'requirement_id','values':[row.id]})
    unknown=Requirement(id='unknown',business_no='row')
    assert not matches(unknown,{'field':'platform','values':['TikTok']})


def test_completed_read_is_saved_before_later_model_timeout(monkeypatch):
    import pytest
    w=Workbook();sheet=w.active;sheet.append(['Header']);sheet.append(['value']);data=binary(w)
    saved=[];calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(1)
            if len(calls)>1:raise TimeoutError('model unavailable')
            return model_response(MappingProposal(mappings=[],requested_ranges=[RangeRequest(sheet=sheet.title,range='A1:A2',reason='evidence')],explanation='read'))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    with pytest.raises(TimeoutError):asyncio.run(propose_mapping(inspect_workbook(data),data,on_progress=lambda p:saved.append(p.model_dump(mode='json'))))
    latest=saved[-1]
    assert latest['read_state']['pages'][sheet.title+'!A1:A2']['complete']
    assert latest['read_state']['evidence'][0]['coverage']['read_cells']==2
    assert latest['complete'] is False

