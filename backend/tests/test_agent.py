import asyncio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from app.models import ChatDecision,ChatEdit
from app.agent import conversation_graph


def test_agent_pauses_for_edit_and_resumes_without_repeating_model(monkeypatch):
    calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            return ChatDecision(answer='将预算改为 40',edits=[ChatEdit(requirement_id='r',values={'budget':'40'})])
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    async def scenario():
        graph=conversation_graph(InMemorySaver())
        config={'configurable':{'thread_id':'tenant:draft:1'}}
        pending=await graph.ainvoke({'messages':[{'role':'user','content':'将 r 预算改为40'}],'draft':{}},config)
        assert pending['__interrupt__']
        assert 'edits' not in pending
        result=await graph.ainvoke(Command(resume=True),config)
        assert result['edits'][0]['values']['budget']=='40'
        assert len(calls)==1
    asyncio.run(scenario())


def test_cancelled_agent_edit_does_not_return_mutations(monkeypatch):
    class Model:
        async def ainvoke(self,_):
            return ChatDecision(answer='修改提案',edits=[ChatEdit(requirement_id='r',values={'budget':'40'})])
    monkeypatch.setattr('app.agent.llm',lambda _:Model())
    async def scenario():
        graph=conversation_graph(InMemorySaver())
        cfg={'configurable':{'thread_id':'tenant:other:1'}}
        await graph.ainvoke({'messages':[],'draft':{}},cfg)
        assert (await graph.ainvoke(Command(resume=False),cfg))['edits']==[]
    asyncio.run(scenario())
