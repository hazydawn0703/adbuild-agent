import asyncio
from uuid import uuid4
import httpx
from app import db
from app.main import app
from app.auth import authenticate
from app.models import BuildDraft,IntakeTask,BudgetAllocation,ChatDecision
from app.provenance import allocation_edits,validate_proposal_evidence
from app.service import edit_draft
from app.intake import save
from test_task_mysql import database
from test_budget_action import allocation_state


def test_allocation_proposal_acceptance_reverifies_message_and_keeps_tree(database):
    state=allocation_state();draft=BuildDraft(id=str(uuid4()),project_id=database,requirements=state['draft']['requirements'])
    with db.transaction() as c:
        tenant=c.execute('select tenant_id from projects where id=%s',(database,)).fetchone()['tenant_id']
        c.execute('insert into drafts(id,tenant_id,project_id,version,data) values(%s,%s,%s,1,%s)',(draft.id,tenant,database,db.json_value(draft)))
    async def setup():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            return (await client.post(f'/projects/{database}/tasks',json={'draft_id':draft.id})).json()
    task=IntakeTask.model_validate(asyncio.run(setup()));task.messages=state['messages']
    allocation=BudgetAllocation(requirement_id='plan',total='100.01',group_weights={'g1':1,'g2':1,'g3':1},message_id='m')
    decision=ChatDecision(answer='',edits=allocation_edits(state,[allocation]));validate_proposal_evidence(state,decision)
    with db.transaction() as c:
        save(c,task)
        actual=edit_draft(c,draft.id,app.dependency_overrides[authenticate](),1,[{**e.model_dump(),'source_message_id':'m'} for e in decision.edits])
        assert str(actual.requirements[0].budget)=='100.01'
        assert [g.ads[0].post_id for g in actual.requirements[0].structure.groups]==['100','200','300']
        assert actual.requirements[0].sources['structure'].kind=='calculated'

