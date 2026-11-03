"""Workbook contracts, independently constructed development/regression inputs."""
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree
import pytest
from openpyxl import Workbook
from app.models import SheetMapping,Binding,RowFilter
from app.excel import (objective,normalize_value,parse_requirements,inspect_workbook,
                       read_region_page,read_workbook,WorkbookReadError,search_rows,parse_export)
from app.templates import capture,reuse


def binary(workbook):
    out=BytesIO();workbook.save(out);return out.getvalue()


def example():
    w=Workbook();meta=w.active;meta.title='Client';meta.append(['Advertiser','7312345678901234567'])
    s=w.create_sheet('Schedule');s.append(['Business','Platform','Post','Budget (USD)','Goal','Period'])
    s.append(['x1','TikTok','https://www.tiktok.com/@demo/video/7712345678901234567',42,'video_views','2027/1/2 09:00 - 2027/1/5 18:30'])
    s.append(['x2','Other','https://other.example/video/7712345678901234568',25,'traffic','2027/1/2 09:00 - 2027/1/5 18:30'])
    m=SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=3,
                   columns={'business_no':'A','post_url':'C','budget':'D','objective':'E'},
                   bindings={'account_id':Binding(kind='cell',sheet='Client',cell='B1'),
                             'currency':Binding(kind='header',cell='D1',transform='currency_code'),
                             'start_at':Binding(kind='row',column='F',transform='interval_start'),
                             'end_at':Binding(kind='row',column='F',transform='interval_end')},
                   filters=[RowFilter(column='B',operator='eq',values=['TikTok'])])
    return w,m


@pytest.mark.parametrize('value',['followers','video_views','traffic'])
def test_canonical_objectives_are_idempotent(value):
    assert normalize_value('objective',normalize_value('objective',value))==value


@pytest.mark.parametrize('value',['followers / video views','traffic + video_views','views / engagement'])
def test_compound_or_underspecified_goal_is_not_first_match(value):
    assert objective(value) is None


def test_fixed_sources_header_currency_platform_filter_and_interval():
    w,m=example();rows=parse_requirements(binary(w),'file',[m])
    assert len(rows)==1
    row=rows[0]
    assert row.account_id=='7312345678901234567' and row.currency=='USD'
    assert row.start_at==datetime(2027,1,2,9) and row.end_at==datetime(2027,1,5,18,30)
    assert row.sources['currency'].cell=='D1' and row.sources['currency'].raw_value=='Budget (USD)'
    assert row.sources['account_id'].sheet=='Client' and row.sources['account_id'].cell=='B1'
    assert row.sources['end_at'].cell=='F2'


@pytest.mark.parametrize('field,value',[('account_id','@staff'),('post_id','#authorization-code'),('currency',42),('language','utm_term=campaign')])
def test_semantic_decoys_cannot_fill_platform_fields(field,value):
    with pytest.raises(ValueError):normalize_value(field,value)


def test_invalid_source_value_remains_visible_and_blocked():
    w,m=example();m.filters=[];m.columns['currency']='D';del m.bindings['currency']
    rows=parse_requirements(binary(w),'f',[m])
    assert rows[0].currency=='' and rows[0].sources['currency'].raw_value=='42'
    assert any(i.code=='invalid_currency' for i in rows[0].issues)


@pytest.mark.parametrize('value,number_format',[('2027-03-14','General'),(datetime(2027,3,14),'yyyy-mm-dd')])
def test_date_only_does_not_create_a_midnight(value,number_format):
    w=Workbook();s=w.active;s.append(['Post','Start']);s.append(['7712345678901234567',value]);s['B2'].number_format=number_format
    rows=parse_requirements(binary(w),'f',[SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=2,columns={'post_id':'A','start_at':'B'})])
    assert rows[0].start_at is None
    assert any(i.field=='start_at' and i.blocking for i in rows[0].issues)
    assert rows[0].sources['start_at'].cell=='B2'


def test_wrong_fixed_source_and_overlapping_field_mapping_rejected():
    w,m=example();m.bindings['account_id'].sheet='Absent'
    with pytest.raises(ValueError,match='工作表不存在'):parse_requirements(binary(w),'f',[m])
    w,m=example();m.columns['currency']='D'
    with pytest.raises(ValueError,match='同时绑定'):parse_requirements(binary(w),'f',[m])


def test_ambiguous_header_currency_does_not_choose_a_code():
    w,m=example();w['Schedule']['D1']='Budget USD / EUR'
    row=parse_requirements(binary(w),'f',[m])[0]
    assert row.currency=='' and any(i.code=='invalid_transform' for i in row.issues)


def test_paged_read_has_complete_coverage_without_expanding_single_read_budget():
    w=Workbook();s=w.active
    for i in range(1,1002):s.cell(i,1,str(i))
    data=binary(w);first=read_region_page(data,s.title,'A1:A1001');last=read_region_page(data,s.title,'A1:A1001',first['next_cursor'])
    assert len(first['cells'])==1000 and not first['complete']
    assert last['cells']=={'A1001':'1001'} and last['complete'] and last['next_cursor'] is None
    with pytest.raises(ValueError):read_region_page(data,s.title,'A1:A1001',1001)


def test_server_search_reports_truncation_and_masks_secrets():
    w=Workbook();s=w.active;s.append(['API key','do-not-show'])
    for n in range(4):s.append(['chosen',str(n)])
    found=search_rows(binary(w),['chosen'],2)
    assert found['matched_rows']==4 and not found['complete'] and len(found['rows'])==2
    assert search_rows(binary(w),['do-not-show'])['matched_rows']==0


def test_template_relocates_fixed_label_and_ignores_unrelated_region_count():
    w,m=example();m.filters=[]
    data=binary(w);template=capture([m],inspect_workbook(data),data)
    w['Client'].title='Renamed account';w['Renamed account'].insert_rows(1,4)
    w.create_sheet('Unrelated').append(['Separate notes'])
    new=binary(w);maps=reuse(template,inspect_workbook(new),new)
    assert maps and maps[0].bindings['account_id'].cell=='B5'
    assert maps[0].bindings['account_id'].sheet=='Renamed account'
    assert maps[0].bindings['currency'].cell=='D1'


def test_batch_filter_cannot_be_saved_as_a_durable_project_default():
    w,m=example();data=binary(w)
    with pytest.raises(ValueError,match='本批筛选'):capture([m],inspect_workbook(data),data)


def test_unreadable_styles_are_a_file_problem_and_original_bytes_are_unchanged():
    w=Workbook();w.active.append(['content']);data=binary(w);out=BytesIO()
    # Construct a format-family failure. Production never performs this mutation.
    with ZipFile(BytesIO(data)) as source,ZipFile(out,'w') as target:
        for item in source.infolist():
            content=source.read(item.filename)
            if item.filename=='xl/styles.xml':
                xml=ElementTree.fromstring(content)
                xml.find('{*}fills/{*}fill').clear()
                content=ElementTree.tostring(xml)
            target.writestr(item,content)
    invalid=out.getvalue();digest=sha256(invalid).hexdigest()
    with pytest.raises(WorkbookReadError) as error:read_workbook(invalid)
    assert error.value.code=='workbook_styles'
    assert sha256(invalid).hexdigest()==digest


def test_export_preserves_budget_levels_without_collapsing_them():
    w=Workbook();s=w.active;s.append(['Campaign ID','Ad Group ID','Ad ID','Post ID','Campaign Budget Amount','Campaign Budget Type','Ad Group Budget Amount','Ad Group Budget Type'])
    s.append(['id:1','id:2','id:3','id:7712345678901234567',100,'Lifetime',40,'Daily'])
    row=parse_export(binary(w))[0]
    assert row['campaign_budget']=='100' and row['adgroup_budget']=='40'
    assert row['campaign_budget_mode']=='lifetime' and row['adgroup_budget_mode']=='daily'
