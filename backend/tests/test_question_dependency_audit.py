import json
from openpyxl import Workbook
from evals.audit_question_dependencies import budget_owner_question


def inputs(tmp_path):
    w=Workbook();s=w.active;s.append(['业务编号','广告组','预算层级','计划总额','广告组预算'])
    for name in ['A','B']:s.append(['BR-1',name,None,None,None])
    w.save(tmp_path/'brief.xlsx');(tmp_path/'paste.txt').write_text('本次仅BR-1。两个组，预算100美元。',encoding='utf-8')
    return {'required_initial_questions':['budget'],'permitted_answers':{'level':'campaign','budget_total':'100'}}


def test_ownership_label_is_audited_without_run_outputs_or_final_plan(tmp_path):
    expected=inputs(tmp_path)
    assert budget_owner_question(tmp_path,expected)['group_count']==2
    expected['permitted_answers']={'level':'adgroup','budget_total':'200','per_group':'100'}
    assert budget_owner_question(tmp_path,expected)['declared_level']=='adgroup'


def test_missing_amount_conflicting_amount_and_format_ambiguity_keep_original_requirement(tmp_path):
    expected=inputs(tmp_path)
    for text in ['本次BR-1，预算待定','本次BR-1，预算100或200美元','本次BR-1，预算1,250美元']:
        (tmp_path/'paste.txt').write_text(text,encoding='utf-8')
        assert budget_owner_question(tmp_path,expected) is None
    (tmp_path/'paste.txt').write_text('本次BR-1，预算100美元',encoding='utf-8');expected['permitted_answers']['decimal_separator']='.'
    assert budget_owner_question(tmp_path,expected) is None
