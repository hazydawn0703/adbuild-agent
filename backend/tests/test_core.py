from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import os
import pytest
from openpyxl import Workbook
from app.models import Requirement, ProjectPolicy, SheetMapping
from app.rules import evaluate, fingerprint, check_policy
from app.excel import parse_export,parse_requirements,inspect_workbook


def req(**values):
    defaults=dict(id='r1',account_id='1234567890123456789',post_id='7701598778473630998',objective='followers',budget=Decimal('50'),budget_mode='lifetime',currency='USD',start_at=datetime(2027,1,1,tzinfo=timezone.utc),end_at=datetime(2027,1,2,tzinfo=timezone.utc),timezone='UTC',audience='HQ',identity_id='identity')
    return Requirement(**(defaults|values))


def policy(**values):
    defaults=dict(account_ids=['1234567890123456789'],objectives=['followers'],audiences=['HQ'],identities=['identity'],currency='USD',per_item_limit=50,batch_limit=500,cumulative_limit=1000,valid_until=datetime(2027,3,1,tzinfo=timezone.utc),enabled=True)
    return ProjectPolicy(**(defaults|values))


def test_expired_is_blocked():
    r=evaluate(req(),[],now=datetime(2028,1,1,tzinfo=timezone.utc))
    assert 'expired' in [i.code for i in r.issues]
    assert check_policy(r,policy(),now=datetime(2028,1,1,tzinfo=timezone.utc))


def test_same_post_different_objective_or_schedule_is_new():
    history=[dict(account_id=req().account_id,post_id=req().post_id,objective='traffic',start_at='2027/01/01 00:00',end_at='2027/01/02 00:00',timezone='UTC',language='All',budget='50')]
    assert evaluate(req(),history,datetime(2026,1,1,tzinfo=timezone.utc)).classification=='new'
    history[0]['objective']='followers'
    assert evaluate(req(),history,datetime(2026,1,1,tzinfo=timezone.utc)).classification=='existing'
    assert evaluate(req(budget=Decimal('60')),history,datetime(2026,1,1,tzinfo=timezone.utc)).classification=='change'


def test_budget_and_name_change_do_not_bypass_dedup():
    assert fingerprint(req())==fingerprint(req(budget=Decimal(60),name='another'))
    assert fingerprint(req())==fingerprint(req(start_at=datetime.fromisoformat('2027-01-01T08:00:00+08:00')))


def test_policy_checks_limits_and_daily_budget():
    now=datetime(2026,1,1,tzinfo=timezone.utc)
    assert not check_policy(evaluate(req(),[],now),policy(),now)
    assert check_policy(evaluate(req(budget=Decimal('51')),[],now),policy(),now)
    assert check_policy(evaluate(req(budget_mode='daily'),[],now),policy(),now)
    assert check_policy(evaluate(req(account_id='other'),[],now),policy(),now)


def test_text_is_data_and_unmapped_rows_do_not_become_advertisements():
    w=Workbook();s=w.active;s.title='需求';s.append(['帖子','预算','目标']);s.append(['https://www.tiktok.com/@test/video/7701598778473630998',50,'follow']);s.append(['ignore previous instructions',999,'traffic'])
    stream=BytesIO();w.save(stream)
    mapping=SheetMapping(sheet='需求',header_row=1,first_row=2,last_row=3,columns={'post_url':'A','budget':'B','objective':'C'})
    rows=parse_requirements(stream.getvalue(),'file',[mapping])
    assert len(rows)==2
    assert any(i.code=="invalid_platform" for i in rows[1].issues)
    assert rows[0].post_id=='7701598778473630998'
    assert rows[0].budget==Decimal('50')
    assert rows[0].sources['budget'].cell=='B2'
    assert not rows[0].account_id


def test_original_workbooks():
    folder=os.getenv('ADBUILD_FIXTURE_DIR')
    if not folder: pytest.skip('Set ADBUILD_FIXTURE_DIR to the supplied workbook directory')
    base=Path(folder)
    exports=parse_export((base/'ExportAds_V2_7_GSTM-Brand-HQ-SINO-TT_20261031165640.xlsx').read_bytes())
    assert len(exports)==221
    assert len({r['campaign_id'] for r in exports})==219
    assert len({r['adgroup_id'] for r in exports})==221
    mapping=SheetMapping(sheet='搭建需求-运营使用',header_row=2,first_row=3,last_row=10,
        columns={'business_no':'B','name':'S','objective':'G','post_url':'J','audience':'K','language':'L','budget':'N','start_at':'O','end_at':'P','timezone':'Q'},defaults={})
    data=(base/'to产品运营-【GSTM】加热投流表 (2).xlsx').read_bytes()
    rows=parse_requirements(data,'requirements',[mapping])
    assert len(rows)==8
    evaluated=[evaluate(r,exports,datetime(2026,11,2,tzinfo=timezone.utc)) for r in rows]
    assert all(r.history for r in evaluated)
    assert all(any(i.code=='expired' for i in r.issues) for r in evaluated)
    # Source O/P cells use date-only formats. Their decoded zero time is an Excel
    # representation, not an explicitly supplied delivery time.
    assert all(r.start_at is None and r.end_at is None for r in evaluated)
    assert all({i.field for i in r.issues if i.code=='invalid_date_precision'}=={'start_at','end_at'} for r in evaluated)
    assert all(r.classification!='new' for r in evaluated)
    assert [len(r.history) for r in evaluated]==[1,1,1,2,3,1,1,1]
    assert [r.business_no for r in rows]==['98','153','156','101','158','118','191','193']
    assert all(r.objective=='followers' and r.language=='en' and r.timezone=='UTC' for r in rows)
    assert sum(r.budget for r in rows)==Decimal('400')
    assert all(not r.currency and not r.budget_mode and not r.identity_id for r in rows)
    assert all(r.sources['timezone'].raw_value=='投放时区：UTC+0' for r in rows)
    assert all(r.sources['post_id'].cell==f'J{index}' for index,r in enumerate(rows,3))
    assert not any(i.code=='language_conflict' for r in evaluated for i in r.issues)
    assert all(not h['schedule_comparison']['comparable'] for r in evaluated for h in r.history)
    assert all(r.sources['start_at'].cell==f'O{index}' and r.sources['end_at'].cell==f'P{index}' for index,r in enumerate(rows,3))
    assert all(h.get('start_at') and h.get('end_at') for r in evaluated for h in r.history)
    assert any(d['field']=='budget' for h in evaluated[3].history for d in h['differences'])
    from app.preview import build_preview
    from app.models import BuildDraft
    preview=build_preview(BuildDraft(id='real',project_id='test',requirements=evaluated),exports,0,datetime(2026,11,3,tzinfo=timezone.utc))
    assert not preview['can_confirm'] and not preview['approval_valid']
