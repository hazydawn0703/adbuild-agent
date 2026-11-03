from types import SimpleNamespace
from app.models import IntakeTask,DemandCandidate,TaskScope
from app.service import apply_additions
import pytest

class DB:
 def __init__(self):self.writes=[]
 def execute(self,sql,args):self.writes.append((sql,args))

def test_message_candidates_are_pending_until_adopted_and_missing_fields_stay_missing(monkeypatch):
 monkeypatch.setattr('app.service.history_for',lambda *a:[])
 task=IntakeTask(id='t',project_id='p',messages=[{'id':'m','role':'user','content':'给帖子 7712345678901234567 涨粉，预算50'}])
 candidate=DemandCandidate(values={'post_id':'7712345678901234567','objective':'followers','budget':'50'})
 assert task.draft_id is None
 db=DB();draft=apply_additions(db,None,{'id':'p','tenant_id':'tenant','account_id':'7312345678901234567'},SimpleNamespace(user_id='u'),[candidate],'m',task)
 assert len(draft.requirements)==1 and draft.requirements[0].budget_mode is None
 assert draft.requirements[0].sources['budget'].message_id=='m' and draft.approval is None
 assert any('insert into drafts' in q for q,_ in db.writes)

def test_candidate_cannot_invent_budget_even_when_user_would_accept_it():
 task=IntakeTask(id='t',project_id='p',messages=[{'id':'m','role':'user','content':'给帖子 7712345678901234567 涨粉'}])
 with pytest.raises(ValueError,match='预算|budget'):
  apply_additions(DB(),None,{'id':'p','tenant_id':'tenant','account_id':''},SimpleNamespace(user_id='u'),[DemandCandidate(values={'post_id':'7712345678901234567','objective':'followers','budget':'80'})],'m',task)
