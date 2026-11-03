from app.history_query import lookup_history


def scope(**changes):
    return dict(status='resolved',action='history',selectors=[{'field':'post_id','values':['77']}],**changes)


def test_history_query_preserves_goals_batches_and_source_without_raw_credentials():
    rows=[dict(post_id='77',objective=goal,ad_id=str(i),source_file='export',source_sheet='Data',source_row=i+2,raw={'Access Token':'private'}) for i,goal in enumerate(['followers','traffic'])]
    result=lookup_history(rows,scope())
    assert result['total']==2 and {r['objective'] for r in result['items']}=={'followers','traffic'}
    assert all('raw' not in r and r['source_file']=='export' for r in result['items'])


def test_history_query_has_explicit_coverage_and_never_defaults_unknown_scope_to_all():
    records=[{'post_id':'77','ad_id':str(i)} for i in range(30)]
    assert lookup_history(records,{})['requires_scope']
    result=lookup_history(records,scope())
    assert result['total']==30 and len(result['items'])==25 and not result['coverage']['complete']


def test_history_query_matches_business_reference_as_a_token_not_another_number():
    selected={'status':'resolved','selectors':[{'field':'business_no','values':['51']}]}
    result=lookup_history([{'name':'51-Launch','ad_id':'a'},{'name':'151-Launch','ad_id':'b'}],selected)
    assert [r['ad_id'] for r in result['items']]==['a']
