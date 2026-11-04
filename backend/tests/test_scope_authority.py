import pytest
from app.scope import validate_scope
from app.agent import validate_selection,ProposalError
from app.models import TaskScope,ScopeSelector,ChatDecision,ChatEdit
from test_entity_assembly import parse


@pytest.mark.parametrize('text',['其余全部只作参考，本次只搭建业务编号 B1','不是全部搭建，只搭建业务编号 B1','其余全部搭建'])
def test_negative_or_unresolved_remainder_is_not_all_records_authority(text):
    with pytest.raises(ValueError,match='肯定依据'):
        validate_scope(TaskScope(status='resolved',action='new',all_records=True,message_ids=['m']),[{'id':'m','role':'user','content':text}])


def test_echoed_question_is_not_user_all_records_authority():
    message={'id':'m','role':'user','content':'本任务回答：是否所有记录全部搭建？仅 B1',
        'answers':[{'field':'scope','question':'是否所有记录全部搭建？','answer':'仅搭建业务编号 B1'}]}
    with pytest.raises(ValueError,match='肯定依据'):validate_scope(TaskScope(status='resolved',action='new',all_records=True,message_ids=['m']),[message])


def test_new_selection_cannot_reuse_only_old_message_reference():
    messages=[{'id':'old','role':'user','content':'本次只搭建业务编号 B1'},{'id':'new','role':'user','content':'本次只搭建业务编号 B2'}]
    scope=TaskScope(status='resolved',action='new',selectors=[ScopeSelector(field='business_no',values=['B1'])],message_ids=['old'])
    with pytest.raises(ValueError,match='当前用户消息'):validate_scope(scope,messages)


def test_old_all_records_does_not_override_new_scope_answer():
    messages=[{'id':'old','role':'user','content':'全部搭建'},
        {'id':'new','role':'user','content':'本任务回答','answers':[{'field':'scope','answer':'仅业务编号 B1'}]}]
    scope=TaskScope(status='resolved',action='new',all_records=True,message_ids=['old','new'])
    with pytest.raises(ValueError,match='肯定依据'):validate_scope(scope,messages)


def test_partial_post_exclusion_cannot_disable_entire_campaign():
    req=parse('adgroup',('G1','G1','G2'),'300')[0]
    scope=TaskScope(status='resolved',action='exclude',selectors=[ScopeSelector(field='post_id',values=['100'])],message_ids=['m'])
    state={'draft':{'requirements':[req.model_dump(mode='json')]},'scope':scope.model_dump(mode='json'),
        'messages':[{'id':'m','role':'user','content':'排除帖子100'}]}
    decision=ChatDecision(answer='',edits=[ChatEdit(requirement_id=req.id,values={'included':False})])
    with pytest.raises(ProposalError,match='部分素材'):validate_selection(state,decision)
