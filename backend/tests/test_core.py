from model_response_fixture import campaign_fixture
from model_response_fixture import row_bindings
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from openpyxl import Workbook
from app.models import Requirement, ProjectPolicy, SheetMapping
from app.rules import evaluate, fingerprint, check_policy
from app.excel import parse_export,parse_requirements,inspect_workbook


def req(**values):
    defaults=dict(id='r1',name='Explicit plan',account_id='1234567890123456789',post_id='7701598778473630998',objective='followers',budget=Decimal('50'),budget_mode='lifetime',currency='USD',start_at=datetime(2027,1,1,tzinfo=timezone.utc),end_at=datetime(2027,1,2,tzinfo=timezone.utc),timezone='UTC',audience='HQ',identity_id='identity')
    return campaign_fixture(**(defaults|values))


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
    mapping=SheetMapping(sheet='需求',header_row=1,first_row=2,last_row=3,bindings=row_bindings({'post_url':'A','budget':'B','objective':'C'}))
    rows=parse_requirements(stream.getvalue(),'file',[mapping])
    assert len(rows)==2
    assert any(i.code=="invalid_platform" for i in rows[1].issues)
    assert rows[0].post_id=='7701598778473630998'
    assert rows[0].budget==Decimal('50')
    assert rows[0].sources['budget'].cell=='B2'
    assert not rows[0].account_id
