"""Read XLSX as data only. No macro execution, external link fetching or formula evaluation."""
from io import BytesIO
from hashlib import sha256
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import re
import zipfile
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries
from pydantic import ValidationError
from urllib.parse import urlsplit
from .models import Requirement, SourceRef, Objective, SheetMapping, Issue


def clean(value):
    return "" if value is None else str(value).strip()


def sensitive_cells(sheet):
    pattern=re.compile(r'^(?:(?:TikTok|Spark(?:\s*Ads)?|广告|账户|账号|登录|登陆|接口|客户)\s*)?(?:access[_ ]?token|refresh[_ ]?token|api[ _-]?key|password|client[_ ]?secret|authorization[_ ]?code|spark[_ ]?code|tt[_ ]?code|密码|密钥|授权码)\s*[:：]?$',re.I)
    rows={};hidden_rows=set();hidden_columns={}
    for c in sheet._cells.values():
        if c.value is not None: rows.setdefault(c.row,[]).append(c)
    for index,cells in rows.items():
        for c in cells:
            if pattern.search(clean(c.value)):
                if len(cells)<=3:hidden_rows.add(index)
                else:hidden_columns[c.column]=min(index,hidden_columns.get(c.column,index))
    return {c.coordinate for cells in rows.values() for c in cells if c.row in hidden_rows or c.column in hidden_columns and c.row>=hidden_columns[c.column]}


def platform_id(value):
    return re.sub(r"^id:\s*", "", clean(value), flags=re.I)


def post_id_from_url(value):
    """Extract the ID only from a TikTok video path, never from a query string."""
    url=urlsplit(clean(value));host=url.hostname or ''
    if host!='tiktok.com' and not host.endswith('.tiktok.com'):return None
    match=re.search(r'(?:^|/)video/([0-9]+)(?:/|$)',url.path)
    return match[1] if match else None


def objective(value):
    text = clean(value).lower()
    if text in Objective._value2member_map_:
        return Objective(text)
    canonical = re.sub(r'[_\s-]+', ' ', text)
    found = set()
    for target,aliases in ((Objective.FOLLOWERS,('follow','page like','买粉','涨粉','community interaction')),
                           (Objective.VIDEO_VIEWS,('video view','视频观看','6 second')),
                           (Objective.TRAFFIC,('traffic','click','流量'))):
        if any(alias in canonical for alias in aliases): found.add(target)
    # A compound objective needs a structural proposal, never first-match-wins.
    return next(iter(found)) if len(found)==1 else None


class WorkbookReadError(ValueError):
    """A recoverable file problem. The original bytes are never rewritten."""
    def __init__(self,code,message):
        self.code=code
        super().__init__(message)


def read_workbook(data: bytes, *, data_only=True):
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("文件超过 20 MB")
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
                raise WorkbookReadError('workbook_size',"解压后文件超过 100 MB")
            if any("vbaProject" in n for n in archive.namelist()):
                raise WorkbookReadError('workbook_macro',"不支持包含宏的文件")
        return load_workbook(BytesIO(data), data_only=data_only, keep_links=False)
    except WorkbookReadError:
        raise
    except Exception as error:
        import traceback
        frames=traceback.extract_tb(error.__traceback__)
        if any('/styles/' in f.filename.replace('\\','/') for f in frames):
            raise WorkbookReadError('workbook_styles','工作簿样式无法读取；原文件已保留，请使用生成软件重新导出 .xlsx 后重试') from error
        raise WorkbookReadError('workbook_format','工作簿内容无法读取；请核对 .xlsx 格式并重新导出，原文件不会被修改') from error


def inspect_workbook(data: bytes):
    wb = read_workbook(data)
    formulas = read_workbook(data,data_only=False)
    sheets = []
    for sheet in wb:
        hidden=sensitive_cells(sheet)
        # _cells avoids allocating millions of styled-but-empty cells through iter_rows.
        populated = sorted((c for c in sheet._cells.values() if c.value is not None),key=lambda c:(c.row,c.column))
        formula_cells = [c for c in formulas[sheet.title]._cells.values() if c.data_type=='f']
        if any(c.row>50000 or c.column>300 for c in populated+formula_cells):
            raise ValueError('实际内容超过 50,000 行或 300 列，请拆分工作表')
        by_row = {}
        cells_by_row = {}
        for c in populated:
            by_row.setdefault(c.row,{})[c.coordinate]='[敏感资料已隐藏]' if c.coordinate in hidden else clean(c.value)[:500]
            cells_by_row.setdefault(c.row,[]).append(c)
        for c in formula_cells:
            if sheet[c.coordinate].value is None:
                by_row.setdefault(c.row,{})[c.coordinate]='[公式缓存缺失]'
        indices=sorted(by_row)
        groups=[]
        for i in indices:
            if not groups or i>groups[-1][-1]+1: groups.append([])
            groups[-1].append(i)
        regions=[]
        for group in groups:
            cells=[c for i in group for c in cells_by_row.get(i,[])]
            lo=min((c.column for c in cells),default=1);hi=max((c.column for c in cells),default=1)
            selected=list(dict.fromkeys(group[:5]+group[-3:]))
            profiles={}
            for c in cells:
                profile=profiles.setdefault(c.column,{'column':get_column_letter(c.column),'nonempty':0,'types':{k:0 for k in ('date','number','error','text')}})
                kind='date' if isinstance(c.value,(date,datetime)) else 'number' if isinstance(c.value,(float,int)) else 'error' if c.data_type=='e' else 'text'
                profile['nonempty']+=1;profile['types'][kind]+=1
            regions.append({'range':f'{get_column_letter(lo)}{group[0]}:{get_column_letter(hi)}{group[-1]}',
                'first_row':group[0],'last_row':group[-1], 'sample':[by_row[i] for i in selected],
                'header_candidates':[{'row':i,'cells':by_row[i]} for i in group[:12]],
                'column_profiles':[profiles[col] for col in sorted(profiles)],
                'nonempty_rows':len(group)})
        sheets.append({'sheet':sheet.title,'rows':max(indices,default=0),'cols':max((c.column for c in populated),default=0),
            'merged':[str(r) for r in sheet.merged_cells.ranges], 'regions':regions,
            'sample':[by_row[i] for i in list(dict.fromkeys(indices[:15]+indices[-5:]))],
            'formula_issues':[c.coordinate for c in formula_cells if sheet[c.coordinate].value is None],
            'errors':[c.coordinate for c in populated if c.data_type=='e']})
    return {'sha256':sha256(data).hexdigest(),'sheets':sheets}


def read_region(data: bytes, sheet: str, cell_range: str):
    wb=read_workbook(data)
    if sheet not in wb.sheetnames: raise ValueError('工作表不存在')
    left,top,right,bottom=range_boundaries(cell_range)
    if top<1 or left<1 or bottom>50000 or right>300 or (bottom-top+1)*(right-left+1)>1000:
        raise ValueError('补读范围最多 1000 个单元格')
    hidden=sensitive_cells(wb[sheet])
    return {c.coordinate:'[敏感资料已隐藏]' if c.coordinate in hidden else clean(c.value)[:500] for row in wb[sheet].iter_rows(min_row=top,max_row=bottom,min_col=left,max_col=right) for c in row if c.value is not None}


def read_region_page(data: bytes,sheet: str,cell_range: str,cursor: int=0):
    """An explicit bounded page. Callers must persist coverage and their overall budget."""
    wb=read_workbook(data)
    if sheet not in wb.sheetnames:raise ValueError('工作表不存在')
    left,top,right,bottom=range_boundaries(cell_range)
    if not all(isinstance(v,int) for v in (left,top,right,bottom)) or top<1 or left<1 or bottom>50000 or right>300 or bottom<top or right<left:raise ValueError('补读范围无效')
    width=right-left+1;total=width*(bottom-top+1)
    if not isinstance(cursor,int) or isinstance(cursor,bool) or cursor<0 or cursor>=total:raise ValueError('补读游标无效')
    stop=min(total,cursor+1000);hidden=sensitive_cells(wb[sheet]);cells={}
    for offset in range(cursor,stop):
        row,col=divmod(offset,width);cell=wb[sheet].cell(top+row,left+col)
        if cell.value is not None:cells[cell.coordinate]='[敏感资料已隐藏]' if cell.coordinate in hidden else clean(cell.value)[:500]
    return {'sheet':sheet,'range':cell_range,'cells':cells,'coverage':{'offset':cursor,'read_cells':stop-cursor,'total_cells':total},'next_cursor':stop if stop<total else None,'complete':stop==total}


def search_rows(data: bytes,terms: list[str],max_hits=30):
    """Server-side source search; results are evidence, never an implicit selection."""
    if not 1<=max_hits<=50:raise ValueError('最多检索 50 行')
    terms={clean(term).casefold() for term in terms if len(clean(term))>=2}
    if not terms:return {'rows':[],'matched_rows':0,'complete':True}
    if len(terms)>30:raise ValueError('一次最多检索 30 个词')
    wb=read_workbook(data);hits=[];context=[];count=0
    patterns=[re.compile(r'(?<![0-9A-Za-z_])'+re.escape(term)+r'(?![0-9A-Za-z_])') for term in terms]
    for sheet in wb:
        hidden=sensitive_cells(sheet);rows={}
        for cell in sheet._cells.values():
            if cell.value is not None and cell.coordinate not in hidden:rows.setdefault(cell.row,[]).append(cell)
        for index,cells in sorted(rows.items()):
            if any(pattern.search(clean(cell.value).casefold()) for pattern in patterns for cell in cells):
                count+=1
                if len(hits)<max_hits:hits.append({'sheet':sheet.title,'row':index,'cells':{c.coordinate:clean(c.value)[:500] for c in cells}})
        hit_indices=[r['row'] for r in hits if r['sheet']==sheet.title]
        if hit_indices:
            context_indices=set(sorted(rows)[:5])
            for hit in hit_indices:
                context_indices.update(index for index in (hit-2,hit-1,hit+1) if index in rows)
            for index in sorted(context_indices):
                if len(context)>=30:break
                cells=rows[index]
                context.append({'sheet':sheet.title,'row':index,'cells':{c.coordinate:clean(c.value)[:500] for c in cells[:40]},'omitted_cells':max(0,len(cells)-40)})
    return {'rows':hits,'context_rows':context,'matched_rows':count,'complete':count<=max_hits,
            'coverage':{'scanned_all_nonempty_rows':True,'returned_matches':len(hits),'total_matches':count,'context_rows':len(context)}}


def rebind_mapping(mapping: SheetMapping, inspection):
    sheet=next((s for s in inspection['sheets'] if s['sheet']==mapping.sheet),None)
    if not sheet: raise ValueError('模板工作表不存在，需要审核')
    region=next((r for r in sheet['regions'] if r['first_row']<=mapping.header_row<=r['last_row']),None)
    if not region: raise ValueError('模板表头区域发生变化，需要审核')
    return mapping.model_copy(update={'first_row':mapping.header_row+1,'last_row':region['last_row']})


def parse_export(data: bytes):
    wb = read_workbook(data)
    records = []
    found_table = False
    for sheet in wb:
        rows = sheet.iter_rows(values_only=True)
        headers = [clean(x) for x in next(rows, [])]
        if not {"Campaign ID", "Ad Group ID", "Ad ID", "Post ID"} <= set(headers):
            continue
        found_table = True
        for row_index, row in enumerate(rows,2):
            d = dict(zip(headers, row))
            if not d.get("Ad ID"):
                continue
            records.append({"source_sheet":sheet.title,"source_row":row_index,"campaign_id": platform_id(d.get("Campaign ID")),
                "adgroup_id": platform_id(d.get("Ad Group ID")), "ad_id": platform_id(d.get("Ad ID")),
                "post_id": platform_id(d.get("Post ID")), "name": clean(d.get("Campaign Name")),
                "objective": objective(d.get("Advertising Objective")),
                "start_at": clean(d.get("Start Time")), "end_at": clean(d.get("End Time")),
                "language": clean(d.get("Languages")), "budget": clean(d.get("Campaign Budget Amount")),
                "budget_mode": clean(d.get("Campaign Budget Type")).lower(),
                "campaign_budget":clean(d.get("Campaign Budget Amount")),
                "campaign_budget_mode":clean(d.get("Campaign Budget Type")).lower(),
                "adgroup_budget":clean(d.get("Ad Group Budget Amount")),
                "adgroup_budget_mode":clean(d.get("Ad Group Budget Type")).lower(),
                "ad_format": clean(d.get("Ad format")), "raw": {k: clean(v) for k,v in d.items()}})
    if not found_table:
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
    if isinstance(value,date) and not isinstance(value,datetime):
        raise ValueError('日期只有某天，缺少明确投放时刻')
    if not isinstance(value, datetime):
        # TikTok exports use non-zero-padded account-local dates such as 2026/9/15 10:08.
        text = clean(value)
        if not re.search(r'[T\s]\d{1,2}:\d{2}',text):
            raise ValueError('日期缺少明确年份或投放时刻')
        if re.fullmatch(r'\d{4}/\d{1,2}/\d{1,2} \d{1,2}:\d{2}(?::\d{2})?',text):
            value = datetime.strptime(text,'%Y/%m/%d %H:%M:%S' if text.count(':')==2 else '%Y/%m/%d %H:%M')
        else:
            value = datetime.fromisoformat(text)
    if value.tzinfo is None and tz:
        value = value.replace(tzinfo=zone_value(tz))
    return value


def normalize_value(field,value,tz=''):
    """The shared input contract for workbook cells, answers and manual edits."""
    if value is None or value=='': return None if field in ('budget','objective','start_at','end_at') else ''
    text=clean(value)
    if field in ('account_id','post_id'):
        result=platform_id(value)
        if not re.fullmatch(r'[0-9]{1,30}',result): raise ValueError('平台 ID 必须是数字字符串，姓名、链接和授权码不能作为 ID')
        return result
    if field=='objective':
        result=objective(value)
        if result is None: raise ValueError('广告目标不支持或包含多个目标，需要明确目标及各自配置')
        return result
    if field=='budget':
        if ',' in text and not re.fullmatch(r'[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?',text): raise ValueError('金额分隔符口径不明确，请提供明确的小数格式')
        try: result=Decimal(text.replace(',',''))
        except InvalidOperation as e: raise ValueError('预算不是有效金额') from e
        if not result.is_finite() or result<=0: raise ValueError('预算必须为有限的正数')
        return result
    if field=='budget_mode':
        result={'总预算':'lifetime','total':'lifetime','lifetime':'lifetime','日预算':'daily','daily':'daily'}.get(text.lower())
        if not result: raise ValueError('预算类型必须明确为总预算或日预算')
        return result
    if field=='currency':
        result=text.upper()
        if not re.fullmatch(r'[A-Z]{3}',result): raise ValueError('币种必须为三个字母的代码，金额不能作为币种')
        return result
    if field=='language':
        result={'英语':'en','英文':'en','english':'en','不限':'all','全部':'all'}.get(text.lower(),text.lower())
        if result!='all' and not re.fullmatch(r'[a-z]{2,3}(?:[-_][a-z]{2,4})?(?:\s*[,;]\s*[a-z]{2,3}(?:[-_][a-z]{2,4})?)*',result):
            raise ValueError('语言需为明确语言代码或 All，UTM、地区和备注不能作为语言')
        return result
    if field=='timezone': return str(zone_value(value))
    if field in ('start_at','end_at'): return date_value(value,tz)
    if field=='post_url':
        host=urlsplit(text).hostname or ''
        if host!='tiktok.com' and not host.endswith('.tiktok.com'): raise ValueError('帖子链接不属于 TikTok')
    if field=='landing_url' and not text.startswith('https://'): raise ValueError('落地页必须为 HTTPS 链接')
    return text


def _anchor(sheet,cell):
    for merged in sheet.merged_cells.ranges:
        if cell.coordinate in merged: return sheet.cell(merged.min_row,merged.min_col)
    return cell


def _transform(value,kind):
    if kind=='value': return value
    text=clean(value)
    if kind=='currency_code':
        matches=re.findall(r'(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])',text)
        if len(set(matches))!=1: raise ValueError('表头没有唯一明确币种代码')
        return matches[0]
    if kind in ('interval_start','interval_end'):
        parts=re.split(r'\s+(?:-|–|—|to)\s+|\s*[至~～]\s*',text,flags=re.I)
        if len(parts)!=2: raise ValueError('排期区间缺少两个可识别的端点')
        return parts[0 if kind=='interval_start' else 1]
    raise ValueError('映射变换类型不支持')


def validate_mapping(mapping,wb):
    """Validate the complete mapping before reading rows, including foreign sources."""
    if mapping.sheet not in wb.sheetnames or mapping.last_row<mapping.first_row: raise ValueError('工作表或数据范围无效')
    if mapping.last_row>wb[mapping.sheet].max_row or mapping.last_row-mapping.first_row>=10000: raise ValueError('数据范围超出工作表或 10,000 行限制')
    if set(mapping.columns)&set(mapping.bindings): raise ValueError('同一字段不能同时绑定列和固定来源')
    for column in mapping.columns.values():
        if not re.fullmatch(r'[A-Z]{1,3}',column) or column_index_from_string(column)>300:raise ValueError('映射列超出允许的300列')
    for binding in mapping.bindings.values():
        source_sheet=binding.sheet or mapping.sheet
        if source_sheet not in wb.sheetnames: raise ValueError('绑定来源工作表不存在')
        if binding.kind=='row':
            if source_sheet!=mapping.sheet or not re.fullmatch(r'[A-Z]{1,3}',binding.column) or column_index_from_string(binding.column)>300: raise ValueError('逐行绑定必须属于当前区域的有效列')
        elif binding.kind in ('cell','header'):
            if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]*',binding.cell): raise ValueError('固定来源必须提供有效单元格')
            cell=wb[source_sheet][binding.cell]
            if cell.row>50000 or cell.column>300: raise ValueError('绑定来源超出允许范围')
        else: raise ValueError('来源绑定类型不支持')
    for rule in mapping.filters:
        if not re.fullmatch(r'[A-Z]{1,3}',rule.column) or column_index_from_string(rule.column)>300 or rule.operator not in ('eq','in','nonempty'): raise ValueError('行筛选无效')
        if rule.operator=='eq' and len(rule.values)!=1 or rule.operator=='in' and not rule.values: raise ValueError('行筛选缺少明确候选值')
    for field,binding in mapping.bindings.items():
        if binding.transform=='value':continue
        source=wb[binding.sheet or mapping.sheet]
        if binding.kind in ('cell','header'):
            cell=_anchor(source,source[binding.cell])
            try:
                if cell.value is None or not clean(cell.value) or cell.data_type=='e':raise ValueError('固定来源为空或为 Excel 错误值')
                _transform(cell.value,binding.transform)
            except ValueError as error:
                raise ValueError(f'{field} 的固定来源 {source.title}!{cell.coordinate} 不支持 {binding.transform}：{error}') from None
        else:
            supported=False
            for index in range(mapping.first_row,mapping.last_row+1):
                if not _row_selected(source,index,mapping.filters):continue
                cell=_anchor(source,source.cell(index,column_index_from_string(binding.column)))
                if cell.value is None or not clean(cell.value) or cell.data_type=='e':continue
                try:_transform(cell.value,binding.transform)
                except ValueError:continue
                supported=True;break
            if not supported:
                raise ValueError(f'{field} 的实际选区没有非空来源支持 {binding.transform}，请更正来源或变换')


def _row_selected(sheet,index,filters):
    for rule in filters:
        value=clean(_anchor(sheet,sheet.cell(index,column_index_from_string(rule.column))).value).casefold()
        if rule.operator=='nonempty':
            if not value:return False
        elif value not in {clean(v).casefold() for v in rule.values}:return False
    return True


def _binding_sources(mapping,wb,index):
    for field,col in mapping.columns.items():
        yield field,wb[mapping.sheet],_anchor(wb[mapping.sheet],wb[mapping.sheet].cell(index,column_index_from_string(col))),'value'
    for field,binding in mapping.bindings.items():
        sheet=wb[binding.sheet or mapping.sheet]
        cell=sheet.cell(index,column_index_from_string(binding.column)) if binding.kind=='row' else sheet[binding.cell]
        yield field,sheet,_anchor(sheet,cell),binding.transform


def parse_requirements(data: bytes, file_id: str, mappings: list[SheetMapping]):
    wb = read_workbook(data)
    formulas = read_workbook(data,data_only=False)
    result = []
    fields = set(Requirement.model_fields) - {"id", "sources", "confirmed_fields", "classification", "history", "issues"}
    hidden_by_sheet={sheet.title:sensitive_cells(sheet) for sheet in wb}
    for mapping in mappings:
        validate_mapping(mapping,wb)
        if (set(mapping.columns)|set(mapping.bindings)|set(mapping.defaults))-fields:
            raise ValueError("存在未知的需求字段")
        sheet = wb[mapping.sheet]
        for index in range(mapping.first_row, mapping.last_row + 1):
            if not _row_selected(sheet,index,mapping.filters): continue
            values, refs, issues = dict(mapping.defaults), {}, []
            for field,source_sheet,cell,transform in _binding_sources(mapping,wb,index):
                value = cell.value
                if cell.coordinate in hidden_by_sheet[source_sheet.title]:
                    issues.append(Issue(code='invalid_sensitive',field=field,message=f'{cell.coordinate} 为敏感资料，不能作为搭建参数'))
                    continue
                if formulas[source_sheet.title][cell.coordinate].data_type=='f' and value is None:
                    issues.append(Issue(code='invalid_formula',field=field,message=f'{cell.coordinate} 公式缺少缓存结果'))
                    continue
                if field in ('post_id','account_id','identity_id') and isinstance(value,(float,int)) and abs(value)>=10**15:
                    issues.append(Issue(code='invalid_precision',field=field,message=f'{cell.coordinate} 长 ID 以数值保存，精度需要核实'))
                    continue
                if cell.data_type == 'e':
                    issues.append(Issue(code='invalid_cell',field=field,message=f'{cell.coordinate} 是 Excel 错误值，不作为搭建参数'))
                    continue
                if field in ('post_url','landing_url') and cell.hyperlink:
                    value = cell.hyperlink.target or value
                if value is None or not clean(value): continue
                refs[field] = SourceRef(file_id=file_id,sheet=source_sheet.title,cell=cell.coordinate,raw_value=clean(value))
                if field in ('start_at','end_at') and isinstance(value,(date,datetime)):
                    # Excel's date serial may decode to midnight even when no time was provided.
                    # Use its number format to preserve that missing precision instead of guessing.
                    if not isinstance(value,datetime) or not re.search(r'[hHsS]',re.sub(r'"[^"]*"','',cell.number_format)):
                        issues.append(Issue(code='invalid_date_precision',field=field,message=f'{cell.coordinate} 只有日期，需明确投放时刻'))
                        continue
                try: values[field]=_transform(value,transform)
                except ValueError as error:
                    issues.append(Issue(code='invalid_transform',field=field,message=f'{cell.coordinate}：{error}'))
            if not any(values.get(k) for k in ('post_url','post_id','business_no','name','budget','objective')):
                continue
            url = clean(values.get('post_url'))
            host=urlsplit(url).hostname or ''
            if url and host!='tiktok.com' and not host.endswith('.tiktok.com'):
                issues.append(Issue(code='invalid_platform',field='post_url',message='帖子链接不属于 TikTok，需核对平台'))
            elif url:
                if values.get('platform') and clean(values['platform']).casefold().replace(' ','') not in ('tiktok','tt'):
                    issues.append(Issue(code='invalid_conflict',field='platform',message='平台字段与 TikTok 帖子链接不一致'))
                if not values.get('platform'):
                    values['platform']='TikTok';refs['platform']=refs['post_url']
                linked=post_id_from_url(url)
                if linked:
                    if values.get('post_id') and platform_id(values['post_id'])!=linked:
                        issues.append(Issue(code='invalid_conflict',field='post_id',message='帖子 ID 与 TikTok 链接中的 ID 不一致'))
                        values['post_id']=''
                    else:
                        values['post_id']=linked;refs['post_id']=refs['post_url']
            if not values.get('platform'):
                # A reviewed platform predicate supplies real row evidence; it is
                # not a workbook-wide default inferred from unrelated records.
                for rule in mapping.filters:
                    labels={clean(v).casefold().replace(' ','') for v in rule.values}
                    if labels and labels<={'tiktok','tt'}:
                        cell=_anchor(sheet,sheet.cell(index,column_index_from_string(rule.column)))
                        actual=clean(cell.value).casefold().replace(' ','')
                        if actual in labels:
                            values['platform']='TikTok';refs['platform']=SourceRef(file_id=file_id,sheet=sheet.title,cell=cell.coordinate,raw_value=clean(cell.value))
                            break
            # Timezone must be normalized before timezone-less timestamps.
            ordered=sorted(list(values),key=lambda field:0 if field=='timezone' else 2 if field in ('start_at','end_at') else 1)
            for field in ordered:
                if field in ('start_at','end_at') and re.fullmatch(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',clean(values[field])):
                    values[field]=None
                    issues.append(Issue(code='invalid_date_precision',field=field,message='日期只有某天，需明确投放时刻'))
                    continue
                try: values[field]=normalize_value(field,values[field],clean(values.get('timezone')))
                except (ValueError,KeyError,TypeError) as error:
                    values[field]=None if field in ('budget','objective','start_at','end_at') else ''
                    issues.append(Issue(code='invalid_'+field,field=field,message=str(error)))
            for field,value in mapping.defaults.items():
                if field not in refs: refs[field]=SourceRef(file_id=file_id,sheet=sheet.title,cell='',raw_value=value,kind='template')
            row_id=sha256(f'{file_id}:{sheet.title}:{index}:{mapping.model_dump_json()}'.encode()).hexdigest()[:24]
            try:
                result.append(Requirement(id=row_id,**values,sources=refs,issues=issues))
            except ValidationError as error:
                for problem in error.errors():
                    field=str(problem['loc'][0]);values.pop(field,None)
                    issues.append(Issue(code='invalid_value',field=field,message=f'{index} 行 {field} 无法识别，需确认'))
                result.append(Requirement(id=row_id,**values,sources=refs,issues=issues))
    return result
