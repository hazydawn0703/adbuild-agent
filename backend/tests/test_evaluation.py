from copy import deepcopy
from app.evaluation import score


def test_scoring_does_not_reward_missing_duplicate_or_manually_corrected_rows():
    expected={'requirements':[{'key':'a','fields':{'budget':'50','post_id':'1234567890123456789'}}]}
    actual=deepcopy(expected)
    assert score(expected,actual)['complete']
    actual['requirements'].append(deepcopy(actual['requirements'][0]))
    assert not score(expected,actual)['complete']
    assert not score(expected,{'requirements':[]})['complete']
    actual=deepcopy(expected);actual['manual_corrections']=1
    assert not score(expected,actual)['complete']


def test_unsupported_sources_and_unnecessary_questions_are_not_hidden():
    expected={'requirements':[{'key':'a','fields':{},'sources':{'budget':['file:Sheet:B2']}}]}
    actual=deepcopy(expected)
    actual['requirements'][0]['sources']['budget'].append('invented')
    actual['questions']=['currency']
    result=score(expected,actual)
    assert not result['complete'] and not result['sources'][0]['correct']
    assert result['unnecessary_questions']==['currency']
