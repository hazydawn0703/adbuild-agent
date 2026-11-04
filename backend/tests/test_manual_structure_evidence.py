from app.manual_structure import accept_structure
from app.models import SourceRef,BuildDraft
from app.preview import build_preview
from app.excel import normalize_value
from test_entity_assembly import parse
import pytest


def test_manual_structure_cannot_forge_file_evidence_and_keeps_removed_candidates():
    req=parse('campaign',('G1','G1','G2'))[0];proposal=req.structure.model_copy(deep=True)
    ad=proposal.groups[0].ads[0];ad.identity_id='12345'
    ad.sources['identity_id']=SourceRef(file_id='other-project-file',sheet='Secret',cell='Z99',raw_value='forged')
    removed=proposal.groups[0].ads.pop()
    result,_=accept_structure(req,proposal,'actor',None)
    source=result.groups[0].ads[0].sources['identity_id']
    assert source.kind=='confirmed' and source.file_id=='' and source.accepted_by=='actor'
    excluded=next(a for g in result.groups for a in g.ads if a.id==removed.id)
    assert not excluded.included
    assert result.groups[0].ads[0].sources['post_id']==req.structure.groups[0].ads[0].sources['post_id']


def test_subcent_group_amount_cannot_be_confirmed_or_rounded_silently():
    req=parse('adgroup',('G1',),'100')[0];req.structure.groups[0].language='en'
    req.structure.groups[0].budget.amount=normalize_value('budget','100')+normalize_value('budget','0.01')/10
    preview=build_preview(BuildDraft(id='d',project_id='p',requirements=[req]),[],0)
    assert not preview['can_confirm'] and preview['campaigns'][0]['planned_total'] is None
    assert any(i['field']=='budget' and i['target_id']==req.structure.groups[0].id for i in preview['issues'])
    with pytest.raises(ValueError,match='两位小数'):normalize_value('budget','100.001')
