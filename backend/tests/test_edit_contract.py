"""Atomic post edits and date precision/expiry are independent input contracts."""
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.models import BuildDraft,Requirement,SourceRef,Issue
from app.rules import evaluate
from app.service import edit_draft


class Result:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row


def connection(monkeypatch):
    draft=BuildDraft(id='draft',project_id='project',requirements=[Requirement(id='row',post_id='7712345678901234567',post_url='https://www.tiktok.com/@demo/video/7712345678901234567',budget=50)])
    class DB:
        def __init__(self):self.updates=[]
        def execute(self,sql,args=()):
            if sql.startswith('select * from drafts'):return Result({'project_id':'project','data':draft.model_dump(mode='json')})
            if sql.startswith('select data from intake_tasks'):return Result(None)
            if sql.startswith('update drafts'):self.updates.append(args);return Result(None)
            pytest.fail('Unexpected DB operation: '+sql)
    monkeypatch.setattr('app.service.project_access',lambda *a,**kw:{'id':'project'})
    monkeypatch.setattr('app.service.history_for',lambda *a:[])
    return DB(),draft


@pytest.mark.parametrize('patch',[{'post_id':'7712345678901234568','post_url':'https://www.tiktok.com/@demo/video/7712345678901234569'},
                                   {'post_id':'7712345678901234568'}])
def test_explicit_post_identity_conflict_is_atomic_422(monkeypatch,patch):
    conn,draft=connection(monkeypatch)
    with pytest.raises(HTTPException) as error:
        edit_draft(conn,'draft',SimpleNamespace(user_id='user'),1,[{'requirement_id':'row','values':{'budget':70}},{'requirement_id':'row','values':patch}])
    assert error.value.status_code==422
    assert not conn.updates
    assert draft.requirements[0].budget==50 and draft.requirements[0].post_id=='7712345678901234567'


def test_url_only_edit_extracts_the_new_id_without_reusing_stale_identity(monkeypatch):
    conn,_=connection(monkeypatch)
    result=edit_draft(conn,'draft',SimpleNamespace(user_id='user'),1,[{'requirement_id':'row','values':{'post_url':'https://www.tiktok.com/@demo/video/7712345678901234568'},'source_message_id':'explicit-edit'}])
    assert result.requirements[0].post_id=='7712345678901234568'
    assert result.requirements[0].sources['post_id'].message_id=='explicit-edit'
    assert len(conn.updates)==1


def test_explicit_matching_post_pair_and_clearing_stale_url_are_valid(monkeypatch):
    for patch in ({'post_id':'7712345678901234568','post_url':'https://www.tiktok.com/@demo/video/7712345678901234568'},
                  {'post_id':'7712345678901234568','post_url':''}):
        conn,_=connection(monkeypatch)
        result=edit_draft(conn,'draft',SimpleNamespace(user_id='user'),1,[{'requirement_id':'row','values':patch}])
        assert result.requirements[0].post_id=='7712345678901234568' and len(conn.updates)==1


def test_tiktok_link_without_resolvable_post_id_requires_clarification(monkeypatch):
    conn,_=connection(monkeypatch)
    with pytest.raises(HTTPException) as error:
        edit_draft(conn,'draft',SimpleNamespace(user_id='user'),1,[{'requirement_id':'row','values':{'post_url':'https://www.tiktok.com/@demo'}}])
    assert error.value.status_code==422 and not conn.updates


def test_video_path_in_query_parameters_does_not_supply_a_post_id(monkeypatch):
    from app.excel import post_id_from_url
    url='https://www.tiktok.com/@demo?redirect=/video/7712345678901234568'
    assert post_id_from_url(url) is None
    conn,_=connection(monkeypatch)
    with pytest.raises(HTTPException) as error:
        edit_draft(conn,'draft',SimpleNamespace(user_id='user'),1,[{'requirement_id':'row','values':{'post_url':url}}])
    assert error.value.status_code==422 and not conn.updates


def date_only(last_day,tz):
    return Requirement(id='row',timezone=tz,issues=[Issue(code='invalid_date_precision',field='end_at',message='time missing')],
                       sources={'end_at':SourceRef(file_id='file',sheet='Schedule',cell='B2',raw_value=last_day)})


@pytest.mark.parametrize('raw',['2026-09-16 00:00:00','2026-09-16','2026/9/16'])
def test_past_whole_date_is_expired_without_inventing_an_end_time(raw):
    row=date_only(raw,'UTC')
    result=evaluate(row,[],datetime(2026,11,3,tzinfo=timezone.utc))
    assert result.end_at is None and result.start_at is None
    assert {'expired','invalid_date_precision'}<={i.code for i in result.issues}
    assert result.sources['end_at'].raw_value==raw


def test_date_expiry_uses_explicit_timezone_and_does_not_guess_today_time():
    moment=datetime(2026,11,3,10,tzinfo=timezone.utc)
    same_local_day=evaluate(date_only('2026-11-03 00:00:00','UTC+14:00'),[],moment)
    assert any(i.code=='expired' for i in same_local_day.issues) # Local date is Nov 4.
    previous_local_day=evaluate(date_only('2026-11-03 00:00:00','UTC-12:00'),[],moment)
    assert not any(i.code=='expired' for i in previous_local_day.issues) # Local date is Nov 2.
    current_day=evaluate(date_only('2026-11-03 00:00:00','UTC'),[],moment)
    assert not any(i.code=='expired' for i in current_day.issues)
    assert all(r.end_at is None for r in (same_local_day,previous_local_day,current_day))


def test_unknown_timezone_or_partial_year_does_not_create_precise_expiry():
    for row in (date_only('2026-09-16 00:00:00',''),date_only('9月16日','UTC')):
        result=evaluate(row,[],datetime(2026,11,3,tzinfo=timezone.utc))
        assert not any(i.code=='expired' for i in result.issues)
        assert any(i.code=='invalid_date_precision' and i.blocking for i in result.issues)
        assert result.classification=='incomplete'
