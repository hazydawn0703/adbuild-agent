import pytest
from fastapi import HTTPException
from app.auth import project_access
from app.executor import bind_value,extract

class Result:
    def __init__(self,row): self.row=row
    def fetchone(self): return self.row
class DB:
    def __init__(self,row): self.row=row
    def execute(self,*args): return Result(self.row)
class Actor: user_id='user'

def test_missing_project_is_not_visible():
    with pytest.raises(HTTPException) as e: project_access(DB(None),'other',Actor())
    assert e.value.status_code==404

def test_viewer_cannot_write_and_operator_cannot_authorize():
    with pytest.raises(HTTPException): project_access(DB({'role':'viewer'}),'p',Actor(),write=True)
    with pytest.raises(HTTPException): project_access(DB({'role':'operator'}),'p',Actor(),admin=True)

def test_bindings_preserve_id_and_numeric_types():
    assert bind_value({'id':'$account_id','budget':'$budget'}, {'account_id':'1234567890123456789','budget':50.0})=={'id':'1234567890123456789','budget':50.0}
    with pytest.raises(KeyError): bind_value('$missing',{})
    assert extract({'list':[{'id':'test'}]},'list.0.id')=='test'
