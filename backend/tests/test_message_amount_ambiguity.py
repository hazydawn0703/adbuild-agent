import pytest
from app.provenance import _budget_values
from decimal import Decimal


@pytest.mark.parametrize('text',['预算100或200美元','预算大约100美元','预算100到200美元','预算至少100美元','budget 100 to 200','budget not 100 but 200','预算不超过100','预算100-200','budget at most 100'])
def test_ambiguous_message_never_proves_first_number(text):
    assert not _budget_values(text)


@pytest.mark.parametrize('text',['预算从100调整为200美元','change budget from 100 to 200','预算调到200美元','预算200美元'])
def test_explicit_correction_keeps_supported_final_amount(text):
    assert _budget_values(text)==[Decimal('200')]
