from datetime import datetime,timezone

from app.intake import refresh
from app.models import Requirement,BuildDraft,IntakeTask,Issue,Clarification
from app.rules import evaluate


def issue(code,message):return Issue(code=code,field='audience',message=message)


def test_same_system_field_and_record_set_has_one_answer_id_and_all_reasons():
    rows=[Requirement(id=rid,issues=[issue('missing','缺少受众'),issue('structure',f'广告组 {rid} 缺少受众')]) for rid in ('row-a','row-b')]
    draft=BuildDraft(id='draft',project_id='project',requirements=rows)
    task=IntakeTask(id='task',project_id='project')
    refresh(task,draft)
    questions=[q for q in task.questions if q.field=='audience']
    assert len(questions)==1 and questions[0].requirement_ids==['row-a','row-b']
    assert all(text in questions[0].question for text in ('缺少受众','广告组 row-a 缺少受众','广告组 row-b 缺少受众'))
    qid=questions[0].id
    refresh(task,draft)
    assert task.questions[0].id==qid
    draft.requirements.reverse()
    refresh(task,draft)
    assert task.questions[0].id==qid


def test_overlapping_but_different_record_sets_are_not_merged():
    rows=[Requirement(id='row-a',issues=[issue('missing','缺少受众'),issue('structure','本组缺少受众')]),
          Requirement(id='row-b',issues=[issue('missing','缺少受众')])]
    task=IntakeTask(id='task',project_id='project')
    refresh(task,BuildDraft(id='draft',project_id='project',requirements=rows))
    questions=[q for q in task.questions if q.field=='audience']
    assert len(questions)==2 and len({q.id for q in questions})==2
    assert {tuple(q.requirement_ids) for q in questions}=={('row-a',),('row-a','row-b')}


def test_semantic_candidate_conflicts_keep_their_own_answer_ids():
    semantic=[Clarification(id='choice-a',field='audience',requirement_ids=['row'],question='请选择A方案',answer_type='choice',options=['A','B']),
              Clarification(id='choice-b',field='audience',requirement_ids=['row'],question='请选择B方案',answer_type='choice',options=['C','D'])]
    task=IntakeTask(id='task',project_id='project',semantic_questions=semantic)
    draft=BuildDraft(id='draft',project_id='project',requirements=[Requirement(id='row',issues=[issue('missing','缺少受众'),issue('structure','广告组缺少受众')])])
    refresh(task,draft)
    assert len(task.questions)==3
    assert [q.id for q in task.questions if q.answer_type=='choice']==['choice-a','choice-b']
    assert [q.options for q in task.questions if q.answer_type=='choice']==[['A','B'],['C','D']]


def test_recomputed_default_structure_retires_both_missing_reasons_after_answer():
    now=datetime(2026,11,3,tzinfo=timezone.utc)
    rows=[evaluate(Requirement(id=rid,post_id='7700000000000000033'),[],now) for rid in ('row-a','row-b')]
    draft=BuildDraft(id='draft',project_id='project',requirements=rows)
    task=IntakeTask(id='task',project_id='project')
    refresh(task,draft)
    assert len([q for q in task.questions if q.field=='audience'])==1
    assert len([q for q in task.questions if q.field=='identity_id'])==1
    for row in draft.requirements:row.audience='Explicit audience';row.identity_id='7700000000000000044'
    draft.requirements=[evaluate(row,[],now) for row in draft.requirements]
    draft.version+=1
    refresh(task,draft)
    assert not any(q.field in ('audience','identity_id') for q in task.questions)
