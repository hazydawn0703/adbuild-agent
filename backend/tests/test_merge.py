from app.models import Requirement, SourceRef, StructureSpec
from app.merge import merge_requirements


def row(id='old',**changes):
    return Requirement.model_validate(dict(id=id,account_id='7800000000000000011',post_id='7800000000000000022',
        objective='followers',business_no='request-17',budget='50',currency='USD',budget_mode='lifetime',
        start_at='2027-01-01T10:00:00Z',end_at='2027-01-04T10:00:00Z',timezone='UTC',included=True)|changes)


def source(value,kind='excel'):
    return SourceRef(file_id='input' if kind=='excel' else '',sheet='Brief' if kind=='excel' else '',
                     cell='A2' if kind=='excel' else '',raw_value=value,kind=kind)


def test_merge_fills_missing_and_preserves_confirmed_value_source_and_structure():
    old=row(audience='',language='',structure={'groups':[{'id':'saved','audience':'Saved audience'}]},confirmed_fields=['budget'],sources={'budget':source('50','confirmed')})
    incoming=row('new',audience='Explicit audience',language='en',sources={'budget':source('50'),'audience':source('Explicit audience'),'language':source('English')})
    before_old=old.model_dump();before_new=incoming.model_dump()
    merged=merge_requirements([old],[incoming])
    assert len(merged)==1 and merged[0].id=='old'
    assert merged[0].audience=='Explicit audience' and merged[0].language=='en'
    assert merged[0].sources['budget'].kind=='confirmed'
    assert merged[0].sources['audience'].cell=='A2'
    assert merged[0].structure.groups[0].id=='saved'
    assert old.model_dump()==before_old and incoming.model_dump()==before_new


def test_current_inputs_conflict_without_overwriting_or_losing_candidate():
    merged=merge_requirements([row(confirmed_fields=['budget'])],[row('new',budget='75')])
    assert len(merged)==2 and str(merged[0].budget)=='50' and str(merged[1].budget)=='75'
    assert not merged[1].included
    assert all(any(i.code=='invalid_conflict' and i.field=='budget' for i in r.issues) for r in merged)


def test_same_business_reference_with_another_post_is_a_conflict():
    merged=merge_requirements([row()],[row('new',post_id='7800000000000000099')])
    assert len(merged)==2 and merged[0].post_id=='7800000000000000022'
    assert not merged[1].included and all(any(i.code=='invalid_conflict' for i in r.issues) for r in merged)


def test_different_goals_and_explicit_batches_remain_independent():
    merged=merge_requirements([row()],[row('view',objective='video_views'),row('next',start_at='2027-02-01T10:00:00Z',end_at='2027-02-04T10:00:00Z')])
    assert len(merged)==3 and all(r.included for r in merged)
    assert not any(i.code=='invalid_conflict' for r in merged for i in r.issues)


def test_unknown_batch_without_business_link_is_not_silently_merged():
    merged=merge_requirements([row(business_no='',start_at=None,end_at=None)],[row('file',business_no='')])
    assert len(merged)==2 and not merged[1].included
    assert all(any(i.code=='invalid_conflict' for i in r.issues) for r in merged)


def test_ambiguous_business_reference_keeps_both_old_values():
    old=[row('a',start_at=None,end_at=None),row('b',start_at=None,end_at=None)]
    merged=merge_requirements(old,[row('file',start_at=None,end_at=None)])
    assert [r.id for r in merged]==['a','b','file']
    assert not merged[-1].included
    assert all(any(i.code=='invalid_conflict' for i in r.issues) for r in merged)


def test_unique_business_link_can_fill_missing_identity():
    merged=merge_requirements([row(post_id='',start_at=None,end_at=None)],[row('file')])
    assert len(merged)==1 and merged[0].post_id=='7800000000000000022' and merged[0].start_at


def test_repeated_equal_records_do_not_duplicate_canonical_values():
    merged=merge_requirements([row()],[row('file-one'),row('file-two')])
    assert len(merged)==1


def test_duplicate_source_id_with_changed_value_keeps_distinct_candidate_id():
    merged=merge_requirements([row()],[row(budget='75')])
    assert len(merged)==2 and len({r.id for r in merged})==2
    assert [r.id for r in merged]==[r.id for r in merge_requirements([row()],[row(budget='75')])]


def test_confirmed_clear_is_not_refilled_without_clarification():
    merged=merge_requirements([row(language='',confirmed_fields=['language'])],[row('file',language='en')])
    assert len(merged)==2 and merged[0].language=='' and not merged[1].included
    assert any(i.field=='language' and i.code=='invalid_conflict' for i in merged[0].issues)
