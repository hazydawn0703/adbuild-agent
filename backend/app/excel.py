"""Read XLSX as data only. No macro execution, external link fetching or formula evaluation."""
from io import BytesIO
from hashlib import sha256
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import re
import zipfile
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter
from .models import Requirement, SourceRef, Objective, SheetMapping, Issue


def clean(value):
    return "" if value is None else str(value).strip()


def platform_id(value):
    return re.sub(r"^id:\s*", "", clean(value), flags=re.I)


def objective(value):
    text = clean(value).lower()
    if any(x in text for x in ("follow", "page like", "买粉", "涨粉", "community interaction")):
        return Objective.FOLLOWERS
    if any(x in text for x in ("video view", "视频观看", "6-second")):
        return Objective.VIDEO_VIEWS
    if any(x in text for x in ("traffic", "click", "流量")):
        return Objective.TRAFFIC
    return None


def read_workbook(data: bytes):
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("文件超过 20 MB")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError("解压后文件超过 100 MB")
        if any("vbaProject" in n for n in archive.namelist()):
            raise ValueError("不支持包含宏的文件")
    return load_workbook(BytesIO(data), data_only=True, keep_links=False)


def inspect_workbook(data: bytes):
    wb = read_workbook(data)
    result = []
    for sheet in wb:
        if sheet.max_row > 50000 or sheet.max_column > 300:
            raise ValueError("工作表超过 50,000 行或 300 列")
        rows = []
        for row in sheet.iter_rows():
            cells = {c.coordinate: clean(c.value)[:500] for c in row if c.value is not None}
            if cells:
                rows.append(cells)
            if len(rows) >= 30:
                break
        result.append({"sheet": sheet.title, "rows": sheet.max_row, "cols": sheet.max_column,
                       "merged": [str(r) for r in sheet.merged_cells.ranges], "sample": rows})
    return {"sha256": sha256(data).hexdigest(), "sheets": result}


def parse_export(data: bytes):
    wb = read_workbook(data)
    records = []
    for sheet in wb:
        rows = sheet.iter_rows(values_only=True)
        headers = [clean(x) for x in next(rows, [])]
        if not {"Campaign ID", "Ad Group ID", "Ad ID", "Post ID"} <= set(headers):
            continue
        for row in rows:
            d = dict(zip(headers, row))
            if not d.get("Ad ID"):
                continue
            records.append({"campaign_id": platform_id(d.get("Campaign ID")),
                "adgroup_id": platform_id(d.get("Ad Group ID")), "ad_id": platform_id(d.get("Ad ID")),
                "post_id": platform_id(d.get("Post ID")), "name": clean(d.get("Campaign Name")),
                "objective": objective(d.get("Advertising Objective")),
                "start_at": clean(d.get("Start Time")), "end_at": clean(d.get("End Time")),
                "language": clean(d.get("Languages")), "budget": clean(d.get("Campaign Budget Amount")),
                "budget_mode": clean(d.get("Campaign Budget Type")).lower(),
                "ad_format": clean(d.get("Ad format")), "raw": {k: clean(v) for k,v in d.items()}})
    if not records:
        raise ValueError("未找到 ExportAds 广告明细表")
    return records


def zone_value(tz):
    text = clean(tz)
    offset = re.fullmatch(r'(?:投放时区[:：]\s*)?(?:UTC|GMT)([+-])(\d{1,2})(?::(\d{2}))?',text,re.I)
    if offset:
        hours, minutes = int(offset[2]), int(offset[3] or 0)
        if hours > 14 or minutes > 59 or hours == 14 and minutes:
            raise ValueError('无效的 UTC 偏移')
        return timezone(timedelta(minutes=(hours*60+minutes)*(1 if offset[1]=='+' else -1)))
    return ZoneInfo(text)


def date_value(value, tz):
    if value is None or value == "":
        return None
    if not isinstance(value, datetime):
        # TikTok exports use non-zero-padded account-local dates such as 2026/9/15 10:08.
        text = clean(value)
        if re.fullmatch(r'\d{4}/\d{1,2}/\d{1,2} \d{1,2}:\d{2}(?::\d{2})?',text):
            value = datetime.strptime(text,'%Y/%m/%d %H:%M:%S' if text.count(':')==2 else '%Y/%m/%d %H:%M')
        else:
            value = datetime.fromisoformat(text)
    if value.tzinfo is None and tz:
        value = value.replace(tzinfo=zone_value(tz))
    return value


def parse_requirements(data: bytes, file_id: str, mappings: list[SheetMapping]):
    wb = read_workbook(data)
    result = []
    fields = set(Requirement.model_fields) - {"id", "sources", "confirmed_fields", "classification", "history", "issues"}
    for mapping in mappings:
        if mapping.sheet not in wb.sheetnames or mapping.last_row < mapping.first_row:
            raise ValueError("工作表或数据范围无效")
        if set(mapping.columns) - fields or set(mapping.defaults) - fields:
            raise ValueError("存在未知的需求字段")
        sheet = wb[mapping.sheet]
        if mapping.last_row > sheet.max_row or mapping.last_row - mapping.first_row > 10000:
            raise ValueError("数据范围超出工作表或 10,000 行限制")
        for index in range(mapping.first_row, mapping.last_row + 1):
            values, refs, issues = dict(mapping.defaults), {}, []
            for field, col in mapping.columns.items():
                cell = sheet.cell(index, column_index_from_string(col))
                for merged in sheet.merged_cells.ranges:
                    if cell.coordinate in merged:
                        cell = sheet.cell(merged.min_row,merged.min_col)
                        break
                value = cell.value
                if cell.data_type == 'e':
                    issues.append(Issue(code='invalid_cell',field=field,message=f'{cell.coordinate} 是 Excel 错误值，不作为搭建参数'))
                    continue
                if field in ('post_url','landing_url') and cell.hyperlink:
                    value = cell.hyperlink.target or value
                if value is not None and clean(value):
                    values[field] = value
                    refs[field] = SourceRef(file_id=file_id, sheet=sheet.title, cell=cell.coordinate, raw_value=clean(value))
            if not any(values.get(k) for k in ("post_url", "post_id", "business_no")):
                continue
            url = clean(values.get("post_url"))
            if url and "tiktok.com/" not in url:
                continue
            if url:
                match = re.search(r"/video/(\d+)", url)
                if match:
                    values["post_id"] = match[1]
                    refs["post_id"] = refs.get("post_url", SourceRef(file_id=file_id,sheet=sheet.title,cell="",raw_value=url))
            for field in ("account_id", "post_id", "identity_id", "business_no"):
                if field in values:
                    values[field] = platform_id(values[field])
            values["objective"] = objective(values.get("objective"))
            if values.get('timezone'):
                try:
                    zone = zone_value(values['timezone'])
                    values['timezone'] = str(zone)
                except (ValueError, KeyError):
                    issues.append(Issue(code='invalid_timezone',field='timezone',message='时区无法识别，请确认'))
            if clean(values.get('language')).lower() in ('英语','英文','english','en'):
                values['language'] = 'en'
            if "budget" in values:
                try:
                    values["budget"] = Decimal(clean(values["budget"]))
                    if not values["budget"].is_finite():
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    values["budget"] = None
                    issues.append(Issue(code="invalid_budget",field="budget",message="预算不是有效金额"))
            for field in ("start_at", "end_at"):
                try:
                    values[field] = date_value(values.get(field), clean(values.get("timezone")))
                except (ValueError, KeyError):
                    values[field] = None
                    issues.append(Issue(code="invalid_date",field=field,message="日期或时区需要确认"))
            # Notes in the workbook never supply defaults or grant execution rights.
            for field, value in mapping.defaults.items():
                if field not in refs:
                    refs[field] = SourceRef(file_id=file_id,sheet=sheet.title,cell="",raw_value=value,kind="template")
            row_id = sha256(f"{file_id}:{sheet.title}:{index}".encode()).hexdigest()[:24]
            result.append(Requirement(id=row_id, **values, sources=refs, issues=issues))
    return result
