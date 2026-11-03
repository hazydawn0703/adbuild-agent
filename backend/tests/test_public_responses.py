from model_response_fixture import campaign_fixture
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from app.auth import Actor
from app.models import BuildDraft, Requirement, IntakeTask, ChangeProposal, ChatEdit, StructureSpec
from app.public import public_business, public_agent_state

SECRET='private-material-credential-for-response-contract'


def draft():
    return BuildDraft(id='draft',project_id='project',requirements=[campaign_fixture(**{
        'id':'row','included':True,'account_id':'7800000000000000011','objective':'followers',
        'budget':'20','budget_mode':'lifetime','currency':'USD','start_at':'2027-01-01T09:00:00Z',
        'end_at':'2027-01-04T09:00:00Z','timezone':'UTC','audience':'Verified audience',
        'structure':{'groups':[{'id':'group','ads':[{'id':'ad','kind':'authorization_code','authorization_code':SECRET}]}]}
    })])


def task(d):
    t=IntakeTask(id='task',project_id='project',draft_id=d.id)
    t.proposal=ChangeProposal(id='proposal',draft_version=d.version,edits=[ChatEdit(requirement_id='row',values={'structure':d.requirements[0].structure.model_dump(mode='json')})],reason='Pending structure')
    t.requests={'request':{'result':{'authorization_code':SECRET}}}
    t.lease={'request_id':'request','until':'2027-01-01T00:00:00Z'}
    t.messages=[{'role':'assistant','content':f'Credential: {SECRET}'}]
    return t


def assert_private_absent(value):
    text=json.dumps(value)
    assert SECRET not in text
    def walk(item):
        if isinstance(item,dict):
            assert not {'authorization_code','requests','lease'} & item.keys()
            for nested in item.values():walk(nested)
        if isinstance(item,list):
            for nested in item:walk(nested)
    walk(value)


class Result:
    def __init__(self,rows):self.rows=rows
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows
    def __iter__(self):return iter(self.rows)


def transaction_for(connection):
    @contextmanager
    def tx():yield connection
    return tx


def test_public_projection_preserves_schema_and_private_storage():
    original=draft();t=task(original);before=deepcopy(original.model_dump())
    public=public_business({'draft':original,'task':t,'preview':{'authorization_present':True}})
    assert_private_absent(public)
    assert public['preview']['authorization_present'] is True
    structure=public['draft']['requirements'][0]['structure']
    assert 'authorization_present' not in structure['groups'][0]['ads'][0]
    assert StructureSpec.model_validate(structure).groups[0].ads[0].authorization_code==''
    assert original.model_dump()==before
    assert original.requirements[0].structure.groups[0].ads[0].authorization_code==SECRET


def test_real_workspace_endpoint_projects_draft_task_and_cached_result(monkeypatch):
    from app import main
    d=draft();t=task(d)
    class DB:
        def execute(self,sql,*args):
            if 'from intake_tasks' in sql:return Result([{'id':t.id,'draft_id':d.id,'version':1,'data':t.model_dump(mode='json')}])
            if 'from drafts' in sql:return Result([{'data':d.model_dump(mode='json')}])
            return Result([])
    monkeypatch.setattr(main,'transaction',transaction_for(DB()))
    monkeypatch.setattr(main,'project_access',lambda *a,**k:{'id':'project','role':'admin'})
    output=main.workspace('project',Actor('operator'))
    assert_private_absent(output)
    assert output['drafts'][0]['id']==d.id
    assert output['tasks'][0]['data']['proposal']['id']=='proposal'


def test_real_task_endpoint_projects_private_draft_and_proposal(monkeypatch):
    from app import intake
    from app import service
    d=draft();t=task(d)
    class DB:
        def execute(self,*args):return Result([{'data':d.model_dump(mode='json')}])
    monkeypatch.setattr(intake,'transaction',transaction_for(DB()))
    monkeypatch.setattr(intake,'access',lambda *a,**k:(t,{'id':'project','policy_version':0,'account_id':d.requirements[0].account_id}))
    monkeypatch.setattr(service,'history_for',lambda *a:[])
    monkeypatch.setattr(service,'pending_questions',lambda *a:[])
    output=intake.get_task('task',Actor('operator'))
    assert_private_absent(output)
    assert output['draft']['requirements'][0]['structure']['groups'][0]['ads'][0]['kind']=='authorization_code'


def test_real_structure_proposal_response_hides_code_but_keeps_server_proposal(monkeypatch):
    from app import main,intake
    d=draft();t=IntakeTask(id='task',project_id='project',draft_id=d.id)
    class DB:
        def execute(self,*args):return Result([{'id':'task'}])
    monkeypatch.setattr(main,'transaction',transaction_for(DB()))
    monkeypatch.setattr(main,'draft_access',lambda *a,**k:(d,{'id':'project'}))
    monkeypatch.setattr(intake,'access',lambda *a,**k:(t,{'id':'project'}))
    monkeypatch.setattr(intake,'save',lambda *a:None)
    body=main.StructureProposalBody(expected_version=1,requirement_id='row',structure=d.requirements[0].structure.model_dump(mode='json'))
    output=main.propose_structure('draft',body,Actor('operator'))
    assert_private_absent(output)
    assert t.proposal.edits[0].values['structure']['groups'][0]['ads'][0]['authorization_code']==SECRET
    StructureSpec.model_validate(output['proposal']['edits'][0]['values']['structure'])


def test_agui_state_and_answer_are_sanitized_together():
    d=draft();response={'answer':f'Private value {SECRET}','task':task(d),'draft':d}
    snapshot=public_agent_state(response,d,'request-17')
    assert snapshot['response']['request_id']=='request-17'
    assert_private_absent(snapshot)
    assert '[私密凭证已隐藏]' in snapshot['response']['answer']


def test_authentication_keeps_its_access_token_contract(monkeypatch):
    from app import main,auth
    class DB:
        def execute(self,*args):return Result([{'id':'user','password_hash':'unused','active':True}])
    monkeypatch.setattr(main,'transaction',transaction_for(DB()))
    monkeypatch.setattr(main,'settings',lambda:SimpleNamespace(session_secret='development-session-secret-only-for-unit-test'))
    monkeypatch.setattr(auth,'password_hash',SimpleNamespace(verify=lambda *args:True))
    output=main.login(main.LoginBody(email='qa@example.invalid',password='not-a-real-password'))
    assert isinstance(output['access_token'],str) and output['access_token']
    assert output['expires_in']==86400


def test_validation_errors_never_echo_private_input():
    from app.main import error_detail
    from app.models import MaterialSpec
    from pydantic import ValidationError
    import json
    secret='private-material-value'
    try:MaterialSpec.model_validate({'id':{'authorization_code':secret}})
    except ValidationError as error:
        detail=error_detail(error)
        assert secret not in json.dumps(detail)
        assert all(set(item)<= {'loc','type','msg'} for item in detail)
    else:raise AssertionError('invalid material accepted')

