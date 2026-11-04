import pytest
from fastapi import HTTPException
from app.models import BuildDraft
from app.service import stored_draft


def test_old_stored_draft_cannot_reuse_confirmation_or_edit_contract():
    old={'id':'old','project_id':'p','version':5,'requirements':[]}
    with pytest.raises(HTTPException) as error:stored_draft(old)
    assert error.value.status_code==409
    assert 'protocol_version' not in old


def test_new_stored_draft_has_explicit_protocol():
    draft=BuildDraft(id='new',project_id='p')
    assert stored_draft(draft.model_dump()).protocol_version==4
