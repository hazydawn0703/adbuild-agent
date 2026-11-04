from datetime import datetime,timezone
from decimal import Decimal
import pytest
from app.models import BuildDraft,IntakeTask,GroupSpec,MaterialSpec,Requirement,StructureSpec,ChatDecision
from app.entities import edit_entity
from app.intake import refresh
from app.rules import evaluate
from app.provenance import validate_proposal_evidence
from test_entity_assembly import parse
from test_budget_action import allocation_state


def test_unknown_budget_level_keeps_evidenced_groups_and_materials():
    req=parse('',('G1','G1','G2','G2'))[0]
    assert req.structure is not None
    assert req.structure.budget_level is None
    assert [len(g.ads) for g in req.structure.groups]==[2,2]
    assert any(i.field=='budget_level' and i.blocking for i in req.issues)


def test_different_material_answers_have_independent_questions():
    req=parse('campaign',('G1','G1','G2','G2'))[0]
    for group in req.structure.groups:
        for ad in group.ads:ad.post_id=''
    task=IntakeTask(id='task',project_id='project')
    refresh(task,BuildDraft(id='draft',project_id='project',requirements=[evaluate(req,[])]))
    questions=[q for q in task.questions if q.field=='post_id']
    assert len(questions)==4
    assert all(len(q.target_ids)==1 for q in questions)
    assert len({q.id for q in questions})==4


def test_timezone_answer_resolves_existing_precise_naive_group_times():
    req=Requirement(id='p',structure=StructureSpec(budget_level='campaign',groups=[GroupSpec(id='g',
        start_at=datetime(2027,1,10,9),end_at=datetime(2027,1,15,9),ads=[MaterialSpec(id='a')])]))
    changed=edit_entity(req,'g',{'timezone':'UTC'},'answer-message','user')
    group=changed.structure.groups[0]
    assert group.start_at==datetime(2027,1,10,9,tzinfo=timezone.utc)
    assert group.end_at==datetime(2027,1,15,9,tzinfo=timezone.utc)
    assert req.structure.groups[0].start_at.tzinfo is None


def test_model_cannot_change_budget_ownership_without_an_explicit_source():
    state=allocation_state();row=state['draft']['requirements'][0];row['budget']='100.01'
    for index,group in enumerate(row['structure']['groups']):
        group['budget']={'amount':'33.34' if index<2 else '33.33','currency':'USD','mode':'lifetime'}
    changed={**row['structure'],'budget_level':'campaign','groups':[{**g,'budget':None} for g in row['structure']['groups']]}
    decision=ChatDecision(answer='',edits=[{'requirement_id':row['id'],'values':{'structure':changed}}])
    with pytest.raises(ValueError,match='预算层级'):validate_proposal_evidence(state,decision)


def test_another_materials_answer_cannot_be_reused_as_evidence():
    from app.provenance import supported_source
    message={'id':'m','role':'user','content':'帖子 ID 7712345678901234567',
        'answers':[{'field':'post_id','answer':'7712345678901234567','requirement_ids':['p'],'target_ids':['a1']}]}
    state={'messages':[message]}
    assert supported_source(state,{'id':'a1'},'post_id','7712345678901234567').message_id=='m'
    with pytest.raises(ValueError):supported_source(state,{'id':'a2'},'post_id','7712345678901234567')
    with pytest.raises(ValueError):supported_source(state,{'id':'a2'},'post_id','7712345678901234567',
        {'kind':'confirmed','message_id':'m','file_id':'','sheet':'','cell':'','raw_value':'forged'})


def test_complete_campaign_does_not_need_group_budgets_reconfirmed():
    from app.models import Clarification
    req=parse('campaign',('G1','G1','G2','G2'))[0]
    req=evaluate(req,[])
    task=IntakeTask(id='task',project_id='project',semantic_questions=[Clarification(id='q',field='budget',
        scope='requirements',requirement_ids=[req.id],target_ids=[g.id for g in req.structure.groups],question='组预算为空，确认吗？')])
    refresh(task,BuildDraft(id='d',project_id='project',requirements=[req]))
    assert not any(q.id=='q' for q in task.questions)


def test_explicit_message_budget_conflict_blocks_even_if_model_does_not_ask():
    from app.models import SourceRef
    req=parse('campaign',('G1','G1','G2','G2'))[0];req.budget=Decimal('100.01')
    task=IntakeTask(id='t',project_id='p',messages=[{'id':'m','role':'user','content':'另一份口头记录总额999美元，与表格冲突，请核对'}])
    draft=BuildDraft(id='d',project_id='p',requirements=[req]);refresh(task,draft)
    assert any(q.field=='budget' and q.target_ids==[req.id] for q in task.questions)
    before={q.id for q in task.questions if q.field=='budget'}
    task.messages.append({'id':'newer','role':'user','content':'另一份当前预算888美元，需要核对'})
    refresh(task,draft)
    assert {q.id for q in task.questions if q.field=='budget'}!=before
    req.sources['budget']=SourceRef(kind='confirmed',file_id='',sheet='',cell='',raw_value='100.01',message_id='answer')
    task.messages.append({'id':'answer','role':'user','content':'本批最终预算100.01'})
    refresh(task,draft)
    assert not any(i.code=='message_conflict' for i in req.issues)


def test_explicit_per_group_budget_is_not_misread_as_plan_total():
    from app.provenance import plan_budget_values
    assert plan_budget_values('每组预算100美元，总额300美元')==[Decimal('300')]


def test_schedule_questions_address_only_the_actual_missing_field():
    req=parse('campaign',('G1',))[0];group=req.structure.groups[0]
    group.timezone='';group.start_at=group.start_at.replace(tzinfo=None);group.end_at=group.end_at.replace(tzinfo=None)
    task=IntakeTask(id='t',project_id='p');draft=BuildDraft(id='d',project_id='p',requirements=[evaluate(req,[])])
    refresh(task,draft)
    assert {q.field for q in task.questions if q.field in ('start_at','end_at','timezone')}=={'timezone'}
    req=edit_entity(req,group.id,{'timezone':'UTC'},'m','u')
    req.structure.groups[0].end_at=None
    refresh(task,BuildDraft(id='d',project_id='p',requirements=[evaluate(req,[])]))
    assert {q.field for q in task.questions if q.field in ('start_at','end_at','timezone')}=={'end_at'}
