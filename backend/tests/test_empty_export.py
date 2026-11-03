from io import BytesIO

import pytest
from openpyxl import Workbook

from app.excel import parse_export


def workbook_bytes(rows):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def test_valid_export_without_ads_is_empty_history():
    assert parse_export(workbook_bytes([
        ['Campaign ID', 'Ad Group ID', 'Ad ID', 'Post ID'],
        [None, None, None, None],
    ])) == []


def test_missing_export_headers_are_still_rejected():
    with pytest.raises(ValueError, match='ExportAds'):
        parse_export(workbook_bytes([['Campaign ID', 'Ad Group ID', 'Post ID']]))


def test_nonempty_export_keeps_ad_identity_and_source_row():
    records = parse_export(workbook_bytes([
        ['Campaign ID', 'Ad Group ID', 'Ad ID', 'Post ID'],
        ['7100000000000000001', 'group', 'ad', 'post'],
    ]))
    assert len(records) == 1
    assert records[0]['campaign_id'] == '7100000000000000001'
    assert records[0]['ad_id'] == 'ad'
    assert records[0]['source_row'] == 2

