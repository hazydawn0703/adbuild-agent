import json
from collections import Counter
from datetime import datetime,timedelta
from openpyxl import load_workbook
from evals.generate_generalization import generate
from evals.run_regression import validate_inputs
from app.excel import objective,parse_export
from app.objective_spelling import spelling_candidate


def test_new_materials_cover_recovery_and_ambiguity_with_oracle_outside_model_inputs(tmp_path):
    root=tmp_path/'materials';generate(root,'frozen','2026-11-03T09:00:00+00:00')
    cases,contexts,hashes=validate_inputs(root/'inputs');gold=json.loads((root/'oracle.json').read_text())
    assert len(cases)==90 and len(gold)==90
    assert sum(v['clarification_field'] is None for v in gold.values())==60
    counts=Counter(str(v['family']) for v in gold.values())
    assert all(counts[f]==6 for f in ['duration','relative_time','spelling','history_choice'])
    assert counts['history_ambiguity']==counts['unknown_goal']==5
    for case,wanted in gold.items():
        data=root/'inputs'/case;history=parse_export((data/'export.xlsx').read_bytes());plan=wanted['plans'][0]
        if wanted['family'] in ('history_choice','history_ambiguity'):
            for group in plan['groups']:
                posts={a['post_id'] for a in group['ads']}
                matches=[h for h in history if h['post_id'] in posts]
                assert {h['audience'] for h in matches}=={group['audience'],group['audience'].replace('2-','1-')}
                assert all(h['budget']!=plan['total'] for h in history)
        elif wanted['family']=='spelling':
            sheet=load_workbook(data/'brief.xlsx',data_only=True).worksheets[0]
            rows=list(sheet.values);header_index=next(i for i,row in enumerate(rows) if 'Objective' in row);col=list(rows[header_index]).index('Objective')
            for row in rows[header_index+1:]:
                if row[col] and row[col] not in ('Objective',) and len(row)>col and isinstance(row[col],str):
                    assert spelling_candidate(row[col])==plan['objective']
        elif wanted['family']=='relative_time':
            assert all(datetime.fromisoformat(g['start_at']).utcoffset()==timedelta(0) and g['timezone']=='UTC' for g in plan['groups'])
        elif wanted['family']=='unknown_goal':
            assert contexts[case]['normal_answers']['objective']==plan['objective']
    assert not any('oracle' in name or 'expected' in name for name in hashes)
