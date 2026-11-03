from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
import pytest
from openpyxl import Workbook
from fastapi import HTTPException
from app.models import Requirement,SourceRef,ChatDecision,ChatEdit,BuildDraft
from app.provenance import (validate_proposal_evidence,history_reference,match_history_reference,
                            supported_source,verified_history_file,message_supports)


def source(**patch):
    return SourceRef(**(dict(file_id='requirements',sheet='Schedule',cell='B2',raw_value='50')|patch))


def state(message='把预算改为40',**patch):
    row=Requirement(id='row',account_id='7312345678901234567',post_id='7712345678901234567',budget=50,sources={'budget':source()}).model_dump(mode='json')
    return dict(messages=[{'id':'message','role':'user','content':message}],draft={'requirements':[row]},context={},**patch)


def decision(**values):
    return ChatDecision(answer='proposal',edits=[ChatEdit(requirement_id='row',values=values)])


def historical(**patch):
    return dict(source_file='export',source_sheet='Ads',source_row=2,budget='40',account_id='7312345678901234567')|patch


@pytest.mark.parametrize('values',[{'budget':999},{'account_id':'7312345678901234568'},{'currency':'EUR'},{'start_at':'2027-01-03T00:00:00Z'}])
def test_fabricated_critical_value_cannot_become_a_proposal(values):
    with pytest.raises(ValueError):validate_proposal_evidence(state('请分析这批需求'),decision(**values))


def test_business_id_is_not_an_explicit_budget_answer():
    with pytest.raises(ValueError):validate_proposal_evidence(state('只处理业务编号999，预算待定'),decision(budget=999))


@pytest.mark.parametrize('text,value',[('将预算从50改为40',40),('预算统一调到 USD 40.00',40),('Set the budget to 40',40),('预算：40元',40)])
def test_explicit_equivalent_budget_changes_are_accepted(text,value):
    result=decision(budget=value);validate_proposal_evidence(state(text),result)
    ref=result.edits[0].sources['budget']
    assert ref.kind=='confirmed' and ref.message_id=='message'
    # A checkpoint validation repeat cannot duplicate adoption or lose provenance.
    validate_proposal_evidence(state(text),result)


def test_newer_budget_correction_supersedes_older_message():
    data=state('预算改为40');data['messages'].insert(0,{'id':'older','role':'user','content':'预算改为60'})
    with pytest.raises(ValueError):validate_proposal_evidence(data,decision(budget=60))


def test_existing_verified_same_value_preserves_excel_source():
    data=state('请检查方案');result=decision(budget=Decimal('50.00'))
    validate_proposal_evidence(data,result)
    assert result.edits[0].sources['budget'].kind=='excel'
    assert result.edits[0].sources['budget'].cell=='B2'


def test_project_defaults_are_checked_for_applicability():
    data=state('按项目模板补齐');data['context']={'version':3,'account_ids':['7312345678901234567'],'objectives':[],'defaults':{'currency':'USD'}}
    result=decision(currency='usd');validate_proposal_evidence(data,result)
    assert result.edits[0].sources['currency'].kind=='template'
    data['context']['account_ids']=['other']
    with pytest.raises(ValueError):validate_proposal_evidence(data,decision(currency='USD'))


def test_history_reference_requires_row_field_value_and_relevant_candidate():
    data=state('建议沿用历史预算');item=historical();data['draft']['requirements'][0]['history']=[item]
    ref=history_reference(item,'budget')
    result=decision(budget='40');result.edits[0].sources['budget']=ref
    validate_proposal_evidence(data,result)
    assert result.edits[0].sources['budget'].kind=='history'
    assert match_history_reference(ref,'budget',40,[item])==item
    assert match_history_reference(ref,'budget',999,[item]) is None
    assert match_history_reference(ref.model_copy(update={'cell':'budget:3'}),'budget',40,[item]) is None


def test_real_reference_to_wrong_raw_value_is_rejected():
    data=state('沿用历史');item=historical();data['draft']['requirements'][0]['history']=[item]
    result=decision(budget=999);result.edits[0].sources['budget']=history_reference(item,'budget')
    with pytest.raises(ValueError):validate_proposal_evidence(data,result)


def test_confirmed_reference_must_exist_and_support_the_field_value():
    row=state()['draft']['requirements'][0]
    forged=SourceRef(file_id='',sheet='',cell='',raw_value='999',kind='confirmed',message_id='message')
    with pytest.raises(ValueError):supported_source(state('预算40'),row,'budget',999,forged)
    forged.raw_value='40';forged.message_id='absent'
    with pytest.raises(ValueError):supported_source(state('预算40'),row,'budget',40,forged)


def test_unresolved_relative_date_needs_clarification():
    data=state('从明天开始跑三天')
    with pytest.raises(ValueError):validate_proposal_evidence(data,decision(start_at='2027-04-01T00:00:00Z'))
    assert message_supports('start_at','2027-04-01T08:00:00Z','开始时间 2027-04-01T16:00:00+08:00')


@pytest.mark.parametrize('text,field,value',[('语言改为en','language','en'),('语言改为English','language','en'),('预算类型改为daily','budget_mode','daily')])
def test_equivalent_codes_can_follow_chinese_words_without_spaces(text,field,value):
    assert message_supports(field,value,text)


def export_bytes():
    w=Workbook();s=w.active;s.title='Ads';s.append(['Campaign ID','Ad Group ID','Ad ID','Post ID','Campaign Budget Amount'])
    s.append(['id:1','id:2','id:3','id:7712345678901234567',40]);out=BytesIO();w.save(out);return out.getvalue()


class Result:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value


def test_accepting_verified_history_preserves_original_source_and_adds_adoption(monkeypatch,tmp_path):
    from app.service import edit_draft
    data=export_bytes();path=tmp_path/'export.xlsx';path.write_bytes(data)
    draft=BuildDraft(id='draft',project_id='project',requirements=[Requirement(id='row',budget=50,sources={'budget':source()})])
    file={'id':'export','kind':'export','storage_path':'private/export.xlsx','sha256':sha256(data).hexdigest()}
    class DB:
        def execute(self,sql,args=()):
            if sql.startswith('select * from drafts'):return Result({'project_id':'project','data':draft.model_dump(mode='json')})
            if sql.startswith('select id from files'):return Result({'id':'export'} if args==('export','project') else None)
            if sql.startswith('select id,kind,storage_path,sha256'):return Result(file if args==('export','project') else None)
            if sql.startswith('update drafts'):return Result(None)
            pytest.fail('Unexpected database query: '+sql)
    monkeypatch.setattr('app.service.project_access',lambda *a,**kw:{'id':'project'})
    monkeypatch.setattr('app.service.history_for',lambda *a:[])
    monkeypatch.setattr('app.storage.target',lambda _:path)
    ref=history_reference(historical(),'budget')
    accepted=edit_draft(DB(),'draft',SimpleNamespace(user_id='reviewer'),1,[{'requirement_id':'row','values':{'budget':'40.00'},'sources':{'budget':ref.model_dump(mode='json')}}])
    actual=accepted.requirements[0].sources['budget']
    assert actual.kind=='history' and actual.file_id=='export' and actual.cell=='budget:2'
    assert actual.accepted_by=='reviewer' and actual.accepted_at
    assert accepted.requirements[0].budget==40 and accepted.approval is None
    path.write_bytes(data+b'changed')
    with pytest.raises(ValueError,match='指纹'):verified_history_file(DB(),'project',ref,'budget',40)


def test_wrong_project_cannot_supply_a_historical_reference():
    class DB:
        def execute(self,*args):return Result(None)
    with pytest.raises(ValueError,match='当前项目'):verified_history_file(DB(),'other-project',history_reference(historical(),'budget'),'budget',40)
