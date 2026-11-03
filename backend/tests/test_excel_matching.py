from io import BytesIO
from datetime import datetime,timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from openpyxl import Workbook
from app.models import SheetMapping,Requirement
from app.excel import date_value,parse_requirements
from app.rules import evaluate


def test_mapping_direction_is_validated_before_parsing():
    with pytest.raises(ValidationError):
        SheetMapping(sheet='需求',header_row=1,first_row=2,last_row=2,columns={'B':'business_no'})
    with pytest.raises(ValidationError):
        SheetMapping(sheet='需求',header_row=1,first_row=2,last_row=2,columns={'budget':'budget'})


def test_tiktok_dates_and_explicit_utc_offsets():
    assert date_value('2026/9/5 9:08','UTC')==datetime(2026,9,5,9,8,tzinfo=timezone.utc)
    assert date_value('2026/9/5 9:08:01','UTC+08:00').utcoffset().total_seconds()==28800
    assert date_value(datetime(2026,9,5),'投放时区：UTC+0').tzinfo==timezone.utc
    with pytest.raises(ValueError):date_value('2026-09-05','UTC+25')


def test_merged_values_hyperlinks_and_errors_keep_source_cells():
    w=Workbook();s=w.active;s.title='需求'
    s.append(['目标','帖子','预算','时区','语言']);s.append(['page like买粉','点击帖子',50,'投放时区：UTC+0','英语']);s.append([None,'https://www.tiktok.com/@a/video/123','#DIV/0!','投放时区：UTC+0','英文'])
    s.merge_cells('A2:A3');s['B2'].hyperlink='https://www.tiktok.com/@a/video/456'
    stream=BytesIO();w.save(stream)
    rows=parse_requirements(stream.getvalue(),'file',[SheetMapping(sheet='需求',header_row=1,first_row=2,last_row=3,columns={'objective':'A','post_url':'B','budget':'C','timezone':'D','language':'E'})])
    assert len(rows)==2 and rows[0].post_id=='456' and rows[1].post_id=='123'
    assert rows[1].objective=='followers' and rows[1].sources['objective'].cell=='A2'
    assert rows[1].budget is None and any(i.code=='invalid_cell' for i in rows[1].issues)
    assert rows[0].language=='en' and rows[0].sources['language'].raw_value=='英语'


def test_candidate_overlap_is_not_a_verified_duplicate_and_english_alias_is_equal():
    req=Requirement(id='r',account_id='a',post_id='123',objective='followers',budget=Decimal(50),budget_mode='lifetime',currency='USD',timezone='UTC',audience='HQ',identity_id='i',language='英语',start_at='2027-09-05T00:00:00Z',end_at='2027-09-06T00:00:00Z')
    h=dict(post_id='123',objective='followers',start_at='2027/9/5 9:08',end_at='2027/9/6 23:59',language='en',budget='49',budget_mode='lifetime',ad_id='ad')
    result=evaluate(req,[h],datetime(2026,11,3,tzinfo=timezone.utc))
    assert result.classification=='incomplete'
    assert 'history_review' in [i.code for i in result.issues]
    assert not any(i.code=='language_conflict' for i in result.issues)
    assert {d['field'] for d in result.history[0]['differences']}=={'budget','start_at','end_at'}
    assert not evaluate(req,[dict(h,account_id='another')],datetime(2026,11,3,tzinfo=timezone.utc)).history
