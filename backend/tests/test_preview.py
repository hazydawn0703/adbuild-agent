from model_response_fixture import campaign_fixture
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import asyncio
import pytest
from fastapi import HTTPException
from app.models import BuildDraft,Requirement
from app.preview import build_preview,approve_preview
from app.service import submit_approved_draft,edit_draft
from app.executor import execute

NOW=datetime(2026,11,3,tzinfo=timezone.utc)
def requirement(**changes):
    return campaign_fixture(**(dict(id='row',name='测试计划',account_id='account',post_id='7701598778473630998',objective='followers',budget='50',budget_mode='lifetime',currency='USD',start_at='2027-01-01T00:00:00Z',end_at='2027-01-02T00:00:00Z',timezone='UTC',audience='HQ',identity_id='identity')|changes))
def draft(*rows):
    return BuildDraft(id='draft',project_id='project',requirements=list(rows or [requirement()]))

def test_preview_counts_and_budget_are_the_confirmed_scope():
    d=draft(requirement(),requirement(id='skipped',included=False))
    p=build_preview(d,[],0,NOW)
    assert p['counts']==dict(campaigns=1,adgroups=1,ads=1,skipped=1)
    assert p['totals']==[dict(currency='USD',budget_mode='lifetime',amount='50')]
    assert p['can_confirm']
    approve_preview(d,p,'operator',1,p['digest'],True)
    assert d.status=='approved' and d.approval.approved_by=='operator'
    assert d.approval.snapshot['selected_ids']==['row']
    assert build_preview(d,[],0,NOW)['approval_valid']
    first=d.approval.approved_at
    p=build_preview(d,[],0,NOW)
    approve_preview(d,p,'another_operator',1,p['digest'],True)
    assert d.approval.approved_at==first and d.approval.approved_by=='operator'

def test_unresolved_task_scope_blocks_old_selected_rows_and_invalidates_confirmation():
    from app.service import pending_questions
    class Connection:
        def __init__(self,status):self.status=status
        def execute(self,*_):return self
        def fetchone(self):return {'data':{'scope':{'status':self.status},'semantic_questions':[]}}
    d=draft();p=build_preview(d,[],0,NOW)
    approve_preview(d,p,'operator',1,p['digest'],True)
    assert build_preview(d,[],0,NOW,pending_questions=pending_questions(Connection('resolved'),d.id))['approval_valid']
    blocked=build_preview(d,[],0,NOW,pending_questions=pending_questions(Connection('unresolved'),d.id))
    assert not blocked['can_confirm'] and not blocked['approval_valid']
    assert any(issue['field']=='scope' for issue in blocked['issues'])


def test_server_detected_message_conflict_cannot_be_bypassed_at_confirmation():
    from app.service import pending_questions
    class Connection:
        def execute(self,*_):return self
        def fetchone(self):return {'data':{'scope':{'status':'resolved'},'semantic_questions':[],
            'questions':[{'id':'q','field':'budget','scope':'requirements','requirement_ids':['row'],
                          'question':'用户预算来源冲突','blocking':True}]}}
    d=draft()
    preview=build_preview(d,[],0,NOW,pending_questions=pending_questions(Connection(),d.id))
    assert not preview['can_confirm']
    with pytest.raises(HTTPException):approve_preview(d,preview,'operator',1,preview['digest'],True)

@pytest.mark.parametrize('task_state',[{'lease':{'request_id':'running'}},{'analysis':{'resume_available':True}},{'analysis':{'mapping_error':{'resume_available':True}}}])
def test_inflight_or_failed_analysis_cannot_confirm_previous_preview(task_state):
    from app.service import pending_questions
    class Connection:
        def execute(self,*_):return self
        def fetchone(self):return {'data':{'scope':{'status':'resolved'},**task_state}}
    d=draft();p=build_preview(d,[],0,NOW)
    approve_preview(d,p,'operator',1,p['digest'],True)
    blocked=build_preview(d,[],0,NOW,pending_questions=pending_questions(Connection(),d.id))
    assert not blocked['can_confirm'] and not blocked['approval_valid']

@pytest.mark.parametrize('changes',[{'budget':None},{'currency':''},{'start_at':None},{'identity_id':''},{'end_at':'2026-01-02T00:00:00Z'}])
def test_missing_or_expired_selected_rows_block_confirmation(changes):
    d=draft(requirement(**changes));p=build_preview(d,[],0,NOW)
    assert not p['can_confirm']
    with pytest.raises(HTTPException) as err:approve_preview(d,p,'operator',1,p['digest'],True)
    assert err.value.status_code==422 and not d.approval

def test_duplicate_selection_and_empty_selection_are_blocked():
    assert not build_preview(draft(requirement(),requirement(id='other')),[],0,NOW)['can_confirm']
    assert not build_preview(draft(requirement(included=False)),[],0,NOW)['can_confirm']

def test_acknowledgement_stale_version_and_tampered_digest_are_rejected():
    d=draft();p=build_preview(d,[],0,NOW)
    for version,digest,ack,status in [(1,p['digest'],False,422),(2,p['digest'],True,409),(1,'tampered',True,409)]:
        with pytest.raises(HTTPException) as err:approve_preview(d,p,'operator',version,digest,ack)
        assert err.value.status_code==status
    assert not d.approval

def test_policy_history_and_date_changes_invalidate_review():
    d=draft();p=build_preview(d,[],0,NOW);approve_preview(d,p,'operator',1,p['digest'],True)
    assert not build_preview(d,[],1,NOW)['approval_valid']
    assert not build_preview(d,[],0,datetime(2028,1,1,tzinfo=timezone.utc))['approval_valid']
    d.requirements[0].budget='60'
    # Same version with changed content is also rejected by the digest.
    from decimal import Decimal
    d.requirements[0].budget=Decimal(60)
    assert not build_preview(d,[],0,NOW)['approval_valid']

def test_execution_pause_precedes_database_and_advertising_calls(monkeypatch):
    monkeypatch.setattr('app.settings.settings',lambda:SimpleNamespace(execution_enabled=False))
    with pytest.raises(HTTPException) as error:submit_approved_draft(None,draft(),{})
    assert error.value.status_code==423
    with pytest.raises(ValueError,match='真实搭建暂缓'):asyncio.run(execute('not-a-real-run'))

def test_editing_clears_approval_and_creates_a_new_version(monkeypatch):
    d=draft();p=build_preview(d,[],0,NOW);approve_preview(d,p,'operator',1,p['digest'],True)
    class Result:
        def fetchone(self):return {'project_id':'project','data':d.model_dump(mode='json')}
    class DB:
        def execute(self,*args):return Result()
    monkeypatch.setattr('app.service.project_access',lambda *a,**k:{'id':'project'})
    monkeypatch.setattr('app.service.history_for',lambda *a:[])
    result=edit_draft(DB(),'draft',SimpleNamespace(user_id='operator'),1,[{'requirement_id':'row','values':{'budget':'60'}}])
    assert result.version==2 and result.status=='draft' and result.approval is None


def test_valid_numeric_account_still_needs_project_binding_match():
    d=draft(requirement(account_id='50'))
    p=build_preview(d,[],0,NOW,expected_account_id='7312345678901234567')
    assert not p['can_confirm'] and any(i['code']=='account_binding' for i in p['issues'])
    d.requirements[0].account_id='7312345678901234567'
    assert build_preview(d,[],0,NOW,expected_account_id='7312345678901234567')['can_confirm']
