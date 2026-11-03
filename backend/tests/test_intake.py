import asyncio
from io import BytesIO
from datetime import datetime, timezone
import pytest
from openpyxl import Workbook
from app.excel import inspect_workbook, parse_requirements, rebind_mapping
from app.models import SheetMapping, Requirement, BuildDraft, IntakeTask
from app.intake import refresh


@pytest.mark.parametrize('objective',['followers','video views','traffic'])
@pytest.mark.parametrize('variant',range(20))
def test_development_structural_scenarios(objective,variant):
    """Known development corpus, never presented as a user holdout."""
    w=Workbook();s=w.active;s.title=f'Batch{variant}'
    offset=variant*2
    for _ in range(offset): s.append([])
    headers=['Post','Budget','Objective']
    if variant%2: headers.reverse()
    s.append(headers)
    values={'Post':'https://www.tiktok.com/@test/video/7701598778473630998','Budget':50,'Objective':objective}
    for _ in range(variant+1):s.append([values[h] for h in headers])
    s.cell(900,10).number_format='0.00'
    stream=BytesIO();w.save(stream);data=stream.getvalue()
    inspection=inspect_workbook(data)
    assert inspection['sheets'][0]['rows']==offset+variant+2
    columns={field:chr(65+headers.index(h)) for field,h in [('post_url','Post'),('budget','Budget'),('objective','Objective')]}
    mapping=SheetMapping(sheet=s.title,header_row=offset+1,first_row=offset+2,last_row=offset+2,columns=columns)
    rebound=rebind_mapping(mapping,inspection)
    rows=parse_requirements(data,'file',[rebound])
    assert len(rows)==variant+1
    assert all(r.post_id=='7701598778473630998' and r.budget==50 and r.objective for r in rows)


def test_formula_without_cache_and_long_numeric_id_remain_blocked():
    w=Workbook();s=w.active;s.append(['Post','Budget','ID']);s.append(['https://www.tiktok.com/@a/video/123','=1+2',1234567890123456789])
    stream=BytesIO();w.save(stream)
    rows=parse_requirements(stream.getvalue(),'f',[SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=2,columns={'post_url':'A','budget':'B','identity_id':'C'})])
    assert {i.code for i in rows[0].issues}>={'invalid_formula','invalid_precision'}


def test_questions_group_and_evidence_does_not_copy_values():
    from app.rules import evaluate
    rows=[evaluate(Requirement(id=str(i)),[],datetime(2026,11,3,tzinfo=timezone.utc)) for i in range(2)]
    task=IntakeTask(id='task',project_id='p')
    refresh(task,BuildDraft(id='d',project_id='p',requirements=rows))
    assert next(q for q in task.questions if q.field=='currency').requirement_ids==['0','1']
    assert all(e.state=='missing' for e in task.evidence)
    assert all('value' not in e.model_dump() for e in task.evidence)


def test_worker_disabled_does_not_open_database(monkeypatch):
    from types import SimpleNamespace
    from app.worker import main
    monkeypatch.setattr('app.settings.settings',lambda:SimpleNamespace(execution_enabled=False))
    monkeypatch.setattr('app.worker.transaction',lambda:pytest.fail('worker touched queue'))
    asyncio.run(main())


def test_reviewed_template_reuses_headers_not_sheet_name_columns_or_last_row():
    from app.templates import capture,reuse
    def data(headers,records,title):
        w=Workbook();s=w.active;s.title=title;s.append(headers)
        values={'Post':'https://www.tiktok.com/@a/video/123','Budget':50,'Goal':'followers','Notes':'note'}
        for _ in range(records):s.append([values[h] for h in headers])
        out=BytesIO();w.save(out);return out.getvalue()
    old=data(['Post','Budget','Goal'],1,'A')
    mapping=SheetMapping(sheet='A',header_row=1,first_row=2,last_row=2,columns={'post_url':'A','budget':'B','objective':'C'})
    template=capture([mapping],inspect_workbook(old),old)
    new=data(['Notes','Goal','Budget','Post'],40,'Renamed')
    maps=reuse(template,inspect_workbook(new),new)
    assert maps[0].sheet=='Renamed' and maps[0].last_row==41
    assert maps[0].columns=={'post_url':'D','budget':'C','objective':'B'}


def test_pending_semantic_question_blocks_exact_preview_confirmation():
    from app.preview import build_preview
    from test_preview import requirement
    draft=BuildDraft(id='d',project_id='p',requirements=[requirement()])
    question={'field':'budget','requirement_ids':['row'],'question':'本批预算口径冲突，需确认','blocking':True}
    result=build_preview(draft,[],0,datetime(2026,11,3,tzinfo=timezone.utc),pending_questions=[question])
    assert not result['can_confirm']
    assert result['rows'][0]['field_states']['budget']=='conflict'


def test_credentials_are_excluded_from_model_inspection_and_range_reads():
    import json
    from app.excel import read_region
    w=Workbook();s=w.active;s.append(['API key','example-secret']);s.append([])
    s.append(['Post','Budget','Goal','access_token']);s.append(['https://www.tiktok.com/@a/video/123',50,'followers','another-secret'])
    out=BytesIO();w.save(out);data=out.getvalue()
    assert 'example-secret' not in json.dumps(inspect_workbook(data))
    assert 'another-secret' not in json.dumps(read_region(data,s.title,'D4:D4'))
    assert 'Budget' in json.dumps(inspect_workbook(data))
