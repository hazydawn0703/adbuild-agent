from copy import deepcopy

from evals.score_acceptance import compare


def fixture():
    ad={'post_id':'7312345678901234567','identity_id':'7212345678901234567','cta':'','landing_url':''}
    group={'name':'Group A','audience':'All adults','language':'en','timezone':'UTC',
           'start_at':'2028-01-10T09:00:00Z','end_at':'2028-01-15T09:00:00Z','ads':[ad],
           'budget':None}
    plan={'name':'Plan','objective':'followers','account_id':'7112345678901234567',
          'business_no':'B1','budget_level':'campaign','planned_total':'100.01',
          'budget':{'amount':'100.01','currency':'USD','mode':'lifetime'},'adgroups':[group]}
    expected=deepcopy(plan);expected['groups']=expected.pop('adgroups');expected['total']=expected.pop('planned_total')
    expected.update(currency='USD',mode='lifetime')
    return {'campaigns':[plan],'rows':[{'action':'create','business_no':'B1'}]}, {'plans':[expected]}


def test_semantic_score_rejects_repeated_budget_wrong_scope_and_duplicate_group():
    preview,expected=fixture()
    assert compare(preview,expected)==[]
    preview['campaigns'][0]['planned_total']='300.03'
    preview['rows'][0]['business_no']='UNSELECTED'
    preview['campaigns'][0]['adgroups']*=2
    assert set(compare(preview,expected))=={'total','scope','group_identity'}


def test_semantic_score_rejects_timezone_and_material_identity_changes():
    preview,expected=fixture()
    preview['campaigns'][0]['adgroups'][0]['start_at']='2028-01-10T09:00:00+08:00'
    preview['campaigns'][0]['adgroups'][0]['ads'][0]['identity_id']='OTHER'
    assert set(compare(preview,expected))=={'schedule','material_fields'}

