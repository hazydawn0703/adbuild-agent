from model_response_fixture import campaign_fixture
"""Candidate evidence and duplicate decisions are separate contracts."""
from datetime import datetime,timezone
from decimal import Decimal
from app.models import Requirement,Issue,StructureSpec,GroupSpec,BudgetSpec
from app.rules import evaluate,history_candidates

NOW=datetime(2026,11,3,tzinfo=timezone.utc)


def requirement(**patch):
    return campaign_fixture(**(dict(id='row',name='Explicit plan',account_id='7312345678901234567',post_id='7712345678901234567',business_no='batch-42',
        objective='video_views',budget=Decimal(100),budget_mode='lifetime',currency='USD',
        start_at='2027-02-01T09:00:00+00:00',end_at='2027-02-02T18:00:00+00:00',timezone='UTC',
        audience='reviewed audience',identity_id='identity')|patch))


def history(**patch):
    return dict(account_id='7312345678901234567',post_id='7712345678901234567',business_no='batch-42',
        objective='video_views',budget='100',budget_mode='lifetime',language='All',ad_id='history-ad',
        campaign_id='history-campaign',adgroup_id='history-group',source_file='original-export',source_row=12,
        timezone='UTC',start_at='2027/2/1 09:00',end_at='2027/2/2 18:00')|patch


def test_same_post_other_objective_is_reference_and_not_duplicate():
    result=evaluate(requirement(),[history(objective='traffic')],NOW)
    assert result.classification=='new'
    candidate=result.history[0]
    assert candidate['classification']=='reference' and candidate['same_post'] and not candidate['same_objective']
    assert candidate['reference_use']=='configuration_reference'


def test_explicit_new_schedule_is_new_batch_and_not_duplicate():
    result=evaluate(requirement(),[history(start_at='2027/3/1 09:00',end_at='2027/3/2 18:00')],NOW)
    assert result.classification=='new' and result.history[0]['classification']=='new_batch'
    assert result.history[0]['schedule_comparison']['comparable']


def test_verified_same_delivery_budget_change_is_not_new():
    result=evaluate(requirement(budget=Decimal(120)),[history()],NOW)
    assert result.classification=='change'
    assert result.history[0]['classification']=='config_change'
    assert any(d['field']=='budget' and d['level']=='campaign' for d in result.history[0]['differences'])


def test_name_change_cannot_bypass_verified_duplicate():
    result=evaluate(requirement(name='new name'),[history(name='old name')],NOW)
    assert result.classification=='existing'
    assert result.history[0]['reference_use']=='duplicate_check'


def test_missing_history_account_never_becomes_verified_duplicate():
    result=evaluate(requirement(),[history(account_id='')],NOW)
    assert result.classification=='incomplete'
    candidate=result.history[0]
    assert candidate['classification']=='unverified' and not candidate['account_verified']
    assert not any(e['field']=='account_id' for e in candidate['match_evidence'])
    assert any(i.code=='history_review' for i in result.issues)


def test_unknown_history_timezone_keeps_local_clock_hint_unverified():
    result=evaluate(requirement(),[history(timezone='')],NOW)
    assert result.classification=='incomplete'
    candidate=result.history[0]
    assert candidate['classification']=='unverified' and candidate['schedule_comparison']['status']=='local_overlap'
    assert not candidate['schedule_comparison']['comparable']


def test_explicit_timestamp_offsets_allow_instant_comparison_without_assumed_timezone():
    result=evaluate(requirement(),[history(timezone='',start_at='2027-02-01T17:00:00+08:00',end_at='2027-02-03T02:00:00+08:00')],NOW)
    assert result.classification=='existing'
    assert result.history[0]['schedule_comparison']['comparable']


def test_business_number_without_post_is_weak_evidence_only():
    result=evaluate(requirement(post_id=''),[history()],NOW)
    assert result.history and result.history[0]['classification']=='unverified'
    assert not result.history[0]['same_post']
    assert [e['field'] for e in result.history[0]['match_evidence']]==['business_no','account_id']
    assert result.classification=='incomplete'


def test_account_mismatch_is_filtered_and_all_reasonable_candidates_are_retained():
    rows=[history(ad_id='other-target',objective='traffic'),history(ad_id='another-batch',start_at='2027/4/1 09:00',end_at='2027/4/2 18:00'),history(ad_id='unknown-account',account_id=''),history(ad_id='foreign-account',account_id='7312345678901234568')]
    candidates=history_candidates(requirement(),rows)
    assert [r['ad_id'] for r in candidates]==['other-target','another-batch','unknown-account']
    assert {r['classification'] for r in candidates}=={'reference','new_batch','unverified'}
    assert all(r['source_file']=='original-export' and r['source_row']==12 for r in candidates)


def test_invalid_normalized_identity_does_not_pollute_history():
    req=requirement(account_id='',issues=[Issue(code='invalid_account_id',field='account_id',message='invalid source')])
    assert not history_candidates(req,[history(account_id='')])


def test_group_budget_is_not_compared_with_campaign_budget_or_wrong_group():
    structure=StructureSpec(budget_level='adgroup',groups=[GroupSpec(id='reviewed-group',name='north',budget=BudgetSpec(amount=Decimal(40),mode='daily',currency='USD'))])
    result=history_candidates(requirement(structure=structure),[history(adgroup_name='north',campaign_budget='500',campaign_budget_mode='lifetime',adgroup_budget='35',adgroup_budget_mode='daily')])[0]
    assert result['budgets']['campaign']['amount']=='500'
    assert result['budgets']['adgroup']['amount']=='35'
    assert result['differences']==[{'field':'budget','requested':'40','historical':'35','level':'adgroup','requested_group_id':'reviewed-group'}]
    other=history_candidates(requirement(structure=structure),[history(adgroup_name='south',adgroup_budget='35')])[0]
    assert not any(d.get('level')=='adgroup' for d in other['differences'])


def test_invalid_or_missing_history_time_is_unverified_without_exception():
    for value in ('date unknown','2027-02-01',''):
        result=evaluate(requirement(),[history(start_at=value)],NOW)
        assert result.history[0]['classification']=='unverified'
        assert result.classification=='incomplete'

