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
            bad=Binding(kind='cell',column='B') if len(calls)==1 else Binding(kind='cell',cell='B1')
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


def test_explicit_group_column_omission_is_repaired_before_assembling(monkeypatch):
    w=Workbook();s=w.active;s.append(['Campaign Name','Ad Group Name','Post ID'])
    s.append(['Launch','Segment-1','123']);s.append(['Launch','Segment-1','456']);s.append(['Launch','Segment-2','789'])
    data=binary(w);calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages);bindings=row_bindings({'name':'A','post_id':'C'})
            if len(calls)>1:bindings.update(row_bindings({'group_key':'B'}))
            return model_response(MappingProposal(mappings=[SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=4,bindings=bindings)],explanation='Explicit groups'))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    result=asyncio.run(propose_mapping(inspect_workbook(data),data))
    assert len(calls)==2 and result.complete and result.mappings[0].bindings['group_key'].column=='B'
    assert 'bindings.group_key' in calls[1][-1][1]


def test_header_cta_binding_is_repaired_to_row_data_without_inventing_cta(monkeypatch):
    w=Workbook();s=w.active;s.append(['Campaign Name','Ad Group','Post ID','CTA']);s.append(['Launch','G1','123',None])
    data=binary(w);calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            binding=Binding(kind='cell',cell='D1') if len(calls)==1 else Binding(kind='row',column='D')
            return model_response(MappingProposal(explanation='map actual values',mappings=[SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=2,
                bindings={**row_bindings({'name':'A','group_key':'B','post_id':'C'}),'cta':binding})]))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    mapped=asyncio.run(propose_mapping(inspect_workbook(data),data))
    assert len(calls)==2 and 'bindings.cta' in calls[1][-1][1]
    req=parse_requirements(data,'f',mapped.mappings)[0]
    assert req.structure.groups[0].ads[0].cta==''


def test_present_ad_identity_is_bound_instead_of_becoming_a_user_question(monkeypatch):
    w=Workbook();s=w.active;s.append(['Campaign Name','Ad Group','Post ID','素材身份']);s.append(['Launch','G1','123','999'])
    data=binary(w);calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages);bindings=row_bindings({'name':'A','group_key':'B','post_id':'C'})
            if len(calls)>1:bindings.update(row_bindings({'identity_id':'D'}))
            return model_response(MappingProposal(explanation='map actual data',mappings=[SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=2,bindings=bindings)]))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    mapped=asyncio.run(propose_mapping(inspect_workbook(data),data))
    assert len(calls)==2 and 'bindings.identity_id' in calls[1][-1][1]
    assert parse_requirements(data,'f',mapped.mappings)[0].structure.groups[0].ads[0].identity_id=='999'


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


def test_read_beyond_known_workbook_extent_is_proven_empty_without_pagination():
    from app.excel import read_region_page
    w=Workbook();w.active.append(['Known']);w.active.append(['record'])
    result=read_region_page(binary(w),w.active.title,'A3:V200')
    assert result['complete'] and result['next_cursor'] is None and result['cells']=={}
    assert result['coverage']['basis']=='outside_used_range' and result['coverage']['known_empty_cells']==4356


def test_complete_mapping_with_repeated_satisfied_read_reaches_budget_conflict(monkeypatch):
    w=Workbook();s=w.active;s.append(['Campaign Name','Ad Group Name','Post ID','Budget Level','Campaign Budget','Ad Group Budget'])
    s.append(['Launch','G1','123','campaign','100',None]);s.append(['Launch','G2','456','campaign','101',None]);data=binary(w);calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            return model_response(MappingProposal(mappings=[SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=3,
                bindings=row_bindings({'name':'A','group_key':'B','post_id':'C','budget_level':'D','campaign_budget':'E','adgroup_budget':'F'}))],
                requested_ranges=[RangeRequest(sheet=s.title,range='F2:F3',reason='Check empty group budget')],explanation='Mapping complete',complete=True))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    mapped=asyncio.run(propose_mapping(inspect_workbook(data),data))
    assert mapped.complete and not mapped.requested_ranges and not mapped.pending_ranges and len(calls)==2
    req=parse_requirements(data,'f',mapped.mappings)[0]
    assert len(req.structure.groups)==2 and any(i.field=='budget' for i in req.issues)


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
