"""Read-only lookup over project-isolated imported TikTok exports, never live state."""
import re

FIELDS=('campaign_id','adgroup_id','ad_id','post_id','business_no','name','adgroup_name','objective','audience','budget','budget_mode',
        'adgroup_budget','adgroup_budget_mode','start_at','end_at','language','source_file','source_sheet','source_row','imported_at')


def lookup_history(records,scope,requirements=(),limit=25):
    scope=scope.model_dump(mode='json') if hasattr(scope,'model_dump') else scope or {}
    if scope.get('status')!='resolved' or (not scope.get('selectors') and not scope.get('all_records')):
        return {'items':[],'total':0,'requires_scope':True,'coverage':{'complete':False},'source_note':'请先明确历史查询范围'}
    reqs={r['id']:r for r in requirements}
    def matches(record,selector):
        values=[str(v).strip().casefold() for v in selector['values']]
        field=selector['field']
        if field=='platform':return 'tiktok' in values # This store is explicitly imported as TikTok ExportAds.
        if field=='requirement_id':
            posts={str(reqs[v].get('post_id')) for v in selector['values'] if v in reqs and reqs[v].get('post_id')}
            return str(record.get('post_id')) in posts
        value=str(record.get(field) or '').strip().casefold()
        if value in values:return True
        if field=='business_no' and not value:
            name=str(record.get('name') or '').casefold()
            return any(re.search(r'(?<![0-9A-Za-z_])'+re.escape(v)+r'(?![0-9A-Za-z_])',name) for v in values)
        return False
    found=[r for r in records if all(matches(r,s) for s in scope.get('selectors',[]))
           and not any(matches(r,s) for s in scope.get('exclude_selectors',[]))]
    return {'items':[{k:r[k] for k in FIELDS if k in r} for r in found[:limit]],'total':len(found),
            'requires_scope':False,'coverage':{'returned':min(limit,len(found)),'total':len(found),'complete':len(found)<=limit},
            'source_note':'当前项目已导入的 ExportAds 快照；同广告以最近导入记录为准，不代表后台实时状态或已核实权限'}
