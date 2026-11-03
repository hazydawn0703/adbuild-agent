from model_response_fixture import campaign_fixture
from decimal import Decimal
from datetime import datetime, timezone
import json
import pytest
from app.models import Requirement, BuildDraft, StructureSpec, SourceRef
from app.structure import allocate_budget, campaign_tree, validate_structure
from app.preview import build_preview, approve_preview
from app.rules import evaluate
from app.service import submit_approved_draft
from types import SimpleNamespace
from fastapi import HTTPException

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def requirement(**changes):
    values = dict(id='demand-17', name='Campaign', account_id='7800000000000000011',
                  post_id='7800000000000000022', objective='video_views', budget='101.01',
                  budget_mode='lifetime', currency='USD', start_at='2027-02-01T09:00:00Z',
                  end_at='2027-02-05T09:00:00Z', timezone='UTC', audience='Confirmed audience',
                  identity_id='7800000000000000033')
    return campaign_fixture(**(values | changes))


def split(**changes):
    return StructureSpec.model_validate(dict(budget_level='adgroup', groups=[
        {'id':'north', 'name':'North', 'audience':'North audience', 'budget':{'allocation_ratio':'0.4'}},
        {'id':'south', 'name':'South', 'audience':'South audience', 'budget':{'allocation_ratio':'0.6'},
         'ads':[{'id':'asset-1','post_id':'7800000000000000044'}, {'id':'asset-2','post_id':'7800000000000000055'}]},
    ]) | changes)


def test_missing_structure_is_blocked_without_implicit_group_or_material():
    req=Requirement(id='missing')
    tree,issues=campaign_tree(req)
    assert tree['adgroups']==[]
    assert any(i.field=='structure' and i.blocking for i in issues)


def test_explicit_grouping_cannot_hide_duplicate_demand():
    draft = BuildDraft(id='d',project_id='p',requirements=[requirement(),requirement(id='second', structure={'groups':[{'id':'renamed'}]})])
    preview = build_preview(draft, [], 0, NOW)
    assert not preview['can_confirm']
    assert any(issue['code']=='duplicate' for issue in preview['issues'])


def test_explicit_groups_are_real_nodes_and_budget_is_counted_once():
    d = BuildDraft(id='draft', project_id='project', requirements=[requirement(structure=split())])
    preview = build_preview(d, [], 0, NOW)
    assert preview['can_confirm']
    assert preview['counts'] == {'campaigns':1, 'adgroups':2, 'ads':3, 'skipped':0}
    plan = preview['campaigns'][0]
    assert plan['budget'] is None and plan['planned_total'] == '101.01'
    assert [g['budget']['amount'] for g in plan['adgroups']] == ['40.40', '60.61']
    assert preview['totals'] == [{'currency':'USD', 'budget_mode':'lifetime', 'amount':'101.01'}]
    assert [g['audience'] for g in plan['adgroups']] == ['North audience', 'South audience']
    assert [g['language'] for g in plan['adgroups']] == ['all', 'all']


def test_rounding_preserves_exact_total_and_is_deterministic():
    ratios = [Decimal('.3333'), Decimal('.3333'), Decimal('.3334')]
    result = allocate_budget(Decimal('10.01'), ratios)
    assert result == [Decimal('3.34'), Decimal('3.33'), Decimal('3.34')]
    assert sum(result) == Decimal('10.01')
    assert result == allocate_budget(Decimal('10.01'), ratios)


@pytest.mark.parametrize('ratios', [['0','1'], ['-1','2'], ['NaN','1'], []])
def test_invalid_ratios_never_produce_allocations(ratios):
    with pytest.raises(ValueError):
        allocate_budget(Decimal('10'), [Decimal(v) for v in ratios])


@pytest.mark.parametrize('structure', [
    {'budget_level':'campaign','groups':[{'id':'one','budget':{'amount':'101.01'}}]},
    {'budget_level':'adgroup','groups':[{'id':'one','budget':{'amount':'10'}}]},
    {'budget_level':'adgroup','groups':[{'id':'one','budget':{'amount':'101.01','currency':'EUR'}}]},
    {'budget_level':'adgroup','groups':[{'id':'one','budget':{'amount':'101.01','mode':'daily'}}]},
    {'budget_level':'adgroup','groups':[{'id':'one','budget':{'amount':'101.01','allocation_ratio':'1'}}]},
    {'budget_level':'adgroup','groups':[{'id':'one'}]},
    {'groups':[{'id':'one'},{'id':'one'}]},
    {'groups':[{'id':'one','ads':[{'id':'ad'},{'id':'ad'}]}]},
])
def test_invalid_structure_blocks_confirmation(structure):
    req = requirement(structure=structure)
    assert validate_structure(req)
    preview = build_preview(BuildDraft(id='d', project_id='p', requirements=[req]), [], 0, NOW)
    assert not preview['can_confirm']


def test_material_authorization_is_separate_and_secret_not_in_preview():
    req = requirement(post_id='', post_url='', identity_id='', audience='', structure={
        'groups':[{'id':'group', 'audience':'Explicit group audience', 'ads':[
            {'id':'material','kind':'authorization_code','authorization_code':'private-source-material-code'}]}]})
    tree, issues = campaign_tree(req)
    assert not issues
    ad = tree['adgroups'][0]['ads'][0]
    assert ad['post_id'] == '' and ad['authorization_present']
    assert 'authorization_code' not in ad
    preview = build_preview(BuildDraft(id='d',project_id='p',requirements=[req]), [], 0, NOW)
    assert preview['can_confirm']
    assert 'private-source-material-code' not in json.dumps(preview)
    assert all(c['status']=='unverified' for c in preview['platform_checks'])
    evaluated=evaluate(req,[],NOW)
    assert not any(i.code=='missing' and i.field in ('post_id','identity_id','audience') for i in evaluated.issues)
    assert evaluated.classification=='new'


def test_other_platform_link_unknown_material_and_conflicting_post_are_blocked():
    for material in [
        {'id':'asset', 'kind':'unknown'},
        {'id':'asset', 'kind':'authorization_code'},
        {'id':'asset', 'post_url':'https://other.example/video/7800000000000000022'},
        {'id':'asset', 'post_url':'https://www.tiktok.com/@brand/video/7800000000000000999'},
    ]:
        req = requirement(structure={'groups':[{'id':'group', 'ads':[material]}]})
        assert validate_structure(req)


def test_nested_change_invalidates_approval_and_sources_survive():
    req = requirement(structure=split())
    req.structure.groups[0].sources['audience'] = SourceRef(file_id='file', sheet='Audience table',cell='B7',raw_value='North audience')
    draft = BuildDraft(id='d',project_id='p',requirements=[req])
    preview = build_preview(draft, [], 0, NOW)
    approve_preview(draft, preview, 'operator', 1, preview['digest'], True)
    assert build_preview(draft, [], 0, NOW)['approval_valid']
    assert preview['campaigns'][0]['adgroups'][0]['sources']['audience']['cell'] == 'B7'
    req.structure.groups[0].audience = 'Another audience'
    assert not build_preview(draft, [], 0, NOW)['approval_valid']


def test_structured_draft_cannot_reach_flat_executor_even_if_global_switch_changes(monkeypatch):
    monkeypatch.setattr('app.settings.settings',lambda:SimpleNamespace(execution_enabled=True))
    draft=BuildDraft(id='d',project_id='p',requirements=[requirement(structure=split())])
    with pytest.raises(HTTPException) as error:
        submit_approved_draft(None,draft,{})
    assert error.value.status_code==423
    assert '平台写入验收' in error.value.detail

