from app.agent import ModelServiceError
from model_response_fixture import row_bindings
from model_response_fixture import model_response
"""Server selects controlled conversions per actual cell; invalid fields stay local."""
import asyncio
from contextlib import contextmanager
from datetime import datetime,timezone
from io import BytesIO
from types import SimpleNamespace
import pytest
from openpyxl import Workbook
from app.excel import inspect_workbook,read_workbook,validate_mapping,parse_requirements
from app.models import SheetMapping,Binding,RowFilter,MappingProposal,IntakeTask
from app.templates import capture,reuse


def binary(w):
    out=BytesIO();w.save(out);return out.getvalue()


def interval_mapping(sheet,last_row=2):
    return SheetMapping(sheet=sheet,header_row=1,first_row=2,last_row=last_row,
        bindings=row_bindings({'post_id':'A','timezone':'C','start_at':'B','end_at':'B'}))


def test_single_iso_timestamps_are_read_without_model_conversion_or_repair(monkeypatch):
    from app.agent import propose_mapping
    w=Workbook();s=w.active;s.append(['Post','Start','End','Timezone'])
    s.append(['7712345678901234567','2027-01-02T09:00:00Z','2027-01-05T18:30:00Z','UTC'])
    data=binary(w);calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            mapping=SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=2,
                bindings=row_bindings({'post_id':'A','timezone':'D','start_at':'B','end_at':'C'}))
            return model_response(MappingProposal(mappings=[mapping],explanation='explicit model proposal'))
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    proposal=asyncio.run(propose_mapping(inspect_workbook(data),data))
    assert len(calls)==1
    row=parse_requirements(data,'file',proposal.mappings)[0]
    assert row.start_at==datetime(2027,1,2,9,tzinfo=timezone.utc)
    assert row.end_at==datetime(2027,1,5,18,30,tzinfo=timezone.utc)
    assert row.sources['start_at'].transform=='value'
    assert not any(i.code=='invalid_transform' for i in row.issues)


def test_model_writable_conversion_is_protocol_error_with_one_repair(monkeypatch):
    from app.agent import propose_mapping
    w=Workbook();s=w.active;s.append(['Post','Period','Timezone']);s.append(['7712345678901234567','2027-01-02T09:00:00Z','UTC'])
    data=binary(w);calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            mapping=interval_mapping(s.title).model_dump(mode='json')
            mapping['bindings']['start_at']['transform']='interval_start'
            return model_response({'mappings':[mapping],'explanation':'unsupported'})
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    with pytest.raises(ModelServiceError):
        asyncio.run(propose_mapping(inspect_workbook(data),data))
    assert len(calls)==2
    assert 'transform' in str(calls[1])


def test_mixed_interval_sources_keep_bad_row_with_original_issue():
    w=Workbook();s=w.active;s.append(['Post','Period','Timezone'])
    s.append(['7712345678901234567','2027/1/2 09:00 - 2027/1/5 18:30','UTC'])
    s.append(['7712345678901234568','unknown time','UTC'])
    data=binary(w);mapping=interval_mapping(s.title,3)
    validate_mapping(mapping,read_workbook(data));rows=parse_requirements(data,'file',[mapping])
    assert len(rows)==2 and rows[0].start_at==datetime(2027,1,2,9,tzinfo=timezone.utc)
    assert rows[1].start_at is None and rows[1].end_at is None
    assert {i.field for i in rows[1].issues if i.code in ('invalid_start_at','invalid_end_at')}=={'start_at','end_at'}
    assert rows[1].sources['start_at'].cell=='B3' and rows[1].sources['start_at'].raw_value=='unknown time'


def test_conversion_is_chosen_from_actual_filtered_row():
    w=Workbook();s=w.active;s.append(['Post','Period','Timezone','Platform'])
    s.append(['7712345678901234567','2027/1/2 09:00 - 2027/1/5 18:30','UTC','Other'])
    s.append(['7712345678901234568','2027-01-02T09:00:00Z','UTC','TikTok'])
    mapping=interval_mapping(s.title,3);mapping.filters=[RowFilter(column='D',operator='eq',values=['TikTok'])]
    data=binary(w);validate_mapping(mapping,read_workbook(data))
    rows=parse_requirements(data,'file',[mapping])
    assert len(rows)==1 and rows[0].start_at==datetime(2027,1,2,9,tzinfo=timezone.utc)
    assert rows[0].sources['start_at'].cell=='B3' and rows[0].sources['start_at'].transform=='value'


@pytest.mark.parametrize('header',['Budget','USD / EUR',None,'#DIV/0!'])
def test_invalid_fixed_currency_preserves_verified_post(header):
    w=Workbook();s=w.active;s.append(['Post',header]);s.append(['7712345678901234567',50])
    mapping=SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=2,
                         bindings={**row_bindings({'post_id':'A'}), **{'currency':Binding(kind='cell',cell='B1')}})
    data=binary(w);validate_mapping(mapping,read_workbook(data))
    row=parse_requirements(data,'file',[mapping])[0]
    assert row.post_id=='7712345678901234567' and row.currency==''
    if header is not None:assert any(i.field=='currency' and i.blocking for i in row.issues)


def test_currency_transform_accepts_supported_rows_and_preserves_bad_row():
    w=Workbook();s=w.active;s.append(['Post','Currency evidence'])
    s.append(['7712345678901234567','Budget (USD)']);s.append(['7712345678901234568','unspecified'])
    data=binary(w);mapping=SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=3,
                                       bindings={**row_bindings({'post_id':'A'}), **{'currency':Binding(kind='row',column='B')}})
    rows=parse_requirements(data,'file',[mapping])
    assert rows[0].currency=='USD' and rows[1].currency=='' and len(rows)==2
    assert any(i.code=='invalid_transform' and i.field=='currency' for i in rows[1].issues)


def test_invalid_fixed_transform_keeps_other_verified_fields_in_draft():
    w=Workbook();s=w.active;s.append(['Post','Budget']);s.append(['7712345678901234567',50])
    mapping=SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=2,
        bindings={**row_bindings({'post_id':'A','budget':'B'}),'currency':Binding(kind='cell',cell='B1')})
    rows=parse_requirements(binary(w),'file',[mapping])
    assert rows[0].post_id=='7712345678901234567' and rows[0].budget==50
    assert any(i.code=='invalid_transform' and i.field=='currency' for i in rows[0].issues)


def template_with_changed_data():
    w=Workbook();s=w.active;s.append(['Post','Period','Timezone']);s.append(['7712345678901234567','2027/1/2 09:00 - 2027/1/5 18:30','UTC'])
    original=binary(w);template=capture([interval_mapping(s.title)],inspect_workbook(original),original)
    s['B2']='2027-01-02T09:00:00Z';changed=binary(w)
    return changed,template


def test_confirmed_template_keeps_bindings_but_recomputes_conversion_from_current_values():
    data,template=template_with_changed_data()
    mappings=reuse(template,inspect_workbook(data),data)
    row=parse_requirements(data,'file',mappings)[0]
    assert row.sources['start_at'].transform=='value'
    assert row.start_at==datetime(2027,1,2,9,tzinfo=timezone.utc)


def test_old_template_protocol_requests_new_proposal_and_explicit_review(monkeypatch):
    import app.main as main
    data,template=template_with_changed_data();inspection=inspect_workbook(data)
    template['version']=3
    task=IntakeTask(id='task',project_id='project');calls=[]
    class Result:
        def __init__(self,value):self.value=value
        def fetchone(self):return self.value
        def fetchall(self):return self.value
    class DB:
        def execute(self,sql,args=()):
            if sql.startswith('select * from files'):return Result({'id':'file','project_id':'project','inspection':inspection,'storage_path':'source.xlsx'})
            if sql.startswith('select id from intake_tasks'):return Result({'id':'task'})
            if sql.startswith('select data from mapping_templates'):return Result([{'data':template}])
            pytest.fail('Unexpected query: '+sql)
    @contextmanager
    def transaction():yield DB()
    async def download(_):return data
    async def propose(*args,**kwargs):
        calls.append(1)
        return MappingProposal(mappings=[SheetMapping(sheet='Sheet',header_row=1,first_row=2,last_row=2,bindings=row_bindings({'post_id':'A','start_at':'B','timezone':'C'}))],explanation='new mapping')
    monkeypatch.setattr(main,'transaction',transaction)
    monkeypatch.setattr(main,'project_access',lambda *args,**kwargs:{'id':'project'})
    monkeypatch.setattr('app.intake.access',lambda *args,**kwargs:(task,{'id':'project'}))
    monkeypatch.setattr('app.intake.save',lambda *args:None)
    monkeypatch.setattr('app.storage.download',download)
    monkeypatch.setattr(main,'propose_mapping',propose)
    result=asyncio.run(main.mapping_proposal('file',SimpleNamespace(user_id='user')))
    assert calls==[1] and result['template_review_required']
    assert '已确认模板无法通过' in result['explanation']
    assert result['mappings'][0]['bindings']['start_at']['column']=='B'
    assert task.analysis['mapping']['template_review_required']
