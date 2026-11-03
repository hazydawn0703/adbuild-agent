from decimal import Decimal
from app.models import IntakeTask,BuildDraft,Requirement,Clarification
from app.intake import reconcile_questions
from app.scope import redact_private
from app.provenance import allocation_message,message_supports


def test_post_without_code_does_not_imply_missing_platform_permission():
    assert 'authorization_present' not in redact_private({'kind':'post','authorization_code':''})
    assert redact_private({'kind':'authorization_code','authorization_code':'secret'})['authorization_present']


def test_platform_verification_is_not_a_user_data_question():
    task=IntakeTask(id='t',project_id='p',semantic_questions=[Clarification(id='q',field='post_url',scope='task',reason_code='platform_check',question='权限是否具备？')])
    reconcile_questions(task,BuildDraft(id='d',project_id='p',requirements=[Requirement(id='r')]))
    assert task.semantic_questions==[]


def test_equal_weights_and_total_have_explicit_message_evidence():
    message={'id':'m','role':'user','content':'三个组按1:1:1分配，总额100.01美元，按分计'}
    assert allocation_message({'messages':[message]},[Decimal(1)]*3)==message
    assert message_supports('budget','100.01',message['content'])
    assert allocation_message({'messages':[message]},[Decimal(1),Decimal(2),Decimal(1)]) is None


def test_new_allocation_does_not_reuse_an_old_ratio():
    messages=[{'id':'old','role':'user','content':'预算按1:1分配'}, {'id':'new','role':'user','content':'预算比例还需要确认'}]
    assert allocation_message({'messages':messages},[Decimal(1),Decimal(1)]) is None

