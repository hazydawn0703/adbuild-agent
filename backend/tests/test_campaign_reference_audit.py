import json
from openpyxl import Workbook
from evals.audit_campaign_references import audit


def test_reference_audit_reads_inputs_and_declared_facts_not_observed_outputs(tmp_path):
    inputs=tmp_path/'inputs';case=inputs/'RV001';case.mkdir(parents=True)
    refs=tmp_path/'references';refs.mkdir()
    url='https://www.tiktok.com/@unprovided/video/100'
    ref={'expected':{'permitted_answers':{'post_ids_by_group':{'G1':['100']}},
        'final_result_for_grading':{'campaigns':[{'groups':[{'ads':[{'post_id':'100','post_url':url}]}]}]}}}
    original=json.dumps(ref);(refs/'RV001.json').write_text(original)
    for name in ('brief.xlsx','export.xlsx'):Workbook().save(case/name)
    (case/'paste.txt').write_text('帖子ID需澄清')
    result=audit(inputs,refs,tmp_path/'corrected')
    assert len(result['changes'])==1 and (refs/'RV001.json').read_text()==original
    (case/'paste.txt').write_text('明确提供 '+url)
    assert audit(inputs,refs,tmp_path/'retained')['changes']==[]
