from app.models import IntakeTask,BuildDraft,Clarification
from app.intake import question_observation,reconcile_questions
from app.entities import edit_entity
from test_entity_edits import campaign


def test_question_tracks_the_actual_group_evidence_and_resolves_after_edit():
    req=campaign();draft=BuildDraft(id='d',project_id='p',requirements=[req])
    question=Clarification(id='q',scope='requirements',field='audience',requirement_ids=['c'],target_ids=['g2'],reason_code='conflict',question='核对第二组受众')
    before=question_observation(question,draft)
    unrelated=edit_entity(req,'g1',{'audience':'Other'},'m','u')
    assert question_observation(question,draft.model_copy(update={'requirements':[unrelated]}))==before
    changed=edit_entity(req,'g2',{'audience':'Confirmed'},'m','u')
    task=IntakeTask(id='t',project_id='p',semantic_questions=[question],analysis={'question_basis':{'q':before}})
    reconcile_questions(task,draft.model_copy(update={'requirements':[changed]}))
    assert task.semantic_questions==[]


def test_plan_missing_budget_is_not_a_group_budget_question():
    from app.models import Issue
    from app.intake import refresh
    req=campaign();req.structure.budget_level='campaign'
    req.budget=None;req.issues=[Issue(code='missing',field='budget',message='缺少 budget')]
    for group in req.structure.groups:group.budget=None
    task=IntakeTask(id='t',project_id='p',semantic_questions=[Clarification(id='q',scope='requirements',
        field='budget',requirement_ids=[req.id],target_ids=['g2'],blocking=False,question='组预算是否留空？')])
    refresh(task,BuildDraft(id='d',project_id='p',requirements=[req]))
    assert len(task.questions)==1 and task.questions[0].target_ids==[req.id]
