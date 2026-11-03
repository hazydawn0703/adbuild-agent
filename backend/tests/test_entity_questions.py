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

