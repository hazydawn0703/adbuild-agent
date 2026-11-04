from io import BytesIO
from decimal import Decimal
from openpyxl import Workbook
from app.models import SheetMapping, Requirement, Binding
from app.excel import parse_requirements
from app.preview import build_preview
from app.models import BuildDraft
from app.structure import allocate_budget


def test_model_schema_exposes_entity_binding_keys():
    schema=SheetMapping.model_json_schema()['properties']['bindings']
    assert {'campaign_key','group_key','record_key','budget_level','campaign_budget','adgroup_budget'} <= set(schema['propertyNames']['enum'])
    from app.models import ChatEdit
    assert {'budget_level','structure','budget'} <= set(ChatEdit.model_json_schema()['properties']['values']['propertyNames']['enum'])


def parse(level='campaign', groups=('G1','G2','G3'), amount='100.01'):
    wb=Workbook();s=wb.active
    s.append(['plan','group','level','campaign budget','group budget','post','account','business','goal','mode','currency','start','end','tz','audience','identity'])
    for i,g in enumerate(groups):
        s.append(['Launch',g,level,amount if level=='campaign' else None,amount if level=='adgroup' else None,str(100+i),'123','B1','followers','lifetime','USD','2027-01-10T00:00:00Z','2027-01-12T00:00:00Z','UTC','audience','identity'])
    b=BytesIO();wb.save(b)
    m=SheetMapping(sheet=s.title,header_row=1,first_row=2,last_row=len(groups)+1,bindings={f:Binding(kind="row",column=c) for f,c in zip(
        ['campaign_key','group_key','budget_level','campaign_budget','adgroup_budget','post_id','account_id','business_no','objective','budget_mode','currency','start_at','end_at','timezone','audience','identity_id'],
        list('ABCDEFGHIJKLMNOP'))})
    return parse_requirements(b.getvalue(),'file',[m])


def test_repeated_campaign_budget_counts_once():
    reqs=parse()
    assert len(reqs)==1
    assert reqs[0].budget==Decimal('100.01')
    preview=build_preview(BuildDraft(id='d',project_id='p',requirements=reqs),[],0)
    assert preview['counts']==dict(campaigns=1,adgroups=3,ads=3,skipped=0)
    assert preview['totals'][0]['amount']=='100.01'


def test_repeated_group_budget_counts_once_for_multiple_materials():
    reqs=parse('adgroup',('G1','G1','G1'),'300')
    assert reqs[0].budget==300
    assert len(reqs[0].structure.groups)==1
    assert len(reqs[0].structure.groups[0].ads)==3


def test_distinct_group_budgets_are_summed():
    reqs=parse('adgroup',('G1','G2'),'120')
    assert reqs[0].budget==240
    assert len(reqs)==1


def test_weights_allocate_exact_cents():
    assert allocate_budget(Decimal('100.01'),[Decimal(1)]*3)==[Decimal('33.34'),Decimal('33.34'),Decimal('33.33')]


def test_flat_requirement_cannot_be_confirmed():
    preview=build_preview(BuildDraft(id='d',project_id='p',requirements=[Requirement(id='r')]),[],0)
    assert not preview['can_confirm']
    assert preview['counts']['ads']==0


def test_conflicting_plan_budgets_block_confirmation():
    from app.assembly import assemble
    rows=[]
    for i,amount in enumerate(('100','200')):
        rows.append((Requirement(id=str(i),account_id='123',business_no='B',objective='followers',post_id=str(100+i)),
            {'campaign_key':'Launch','group_key':'G1','budget_level':'campaign','campaign_budget':Decimal(amount)},{}))
    req=assemble(rows)[0]
    assert req.budget is None
    assert any(i.code=='invalid_structure' and i.field=='budget' for i in req.issues)


def test_same_names_in_different_accounts_do_not_merge():
    from app.assembly import assemble
    rows=[(Requirement(id=a,account_id=a,business_no='B'),
           {'campaign_key':'Launch','group_key':'G','budget_level':'campaign'}, {}) for a in ('123','456')]
    assert len(assemble(rows))==2


def test_reviewed_campaign_name_is_identity_without_duplicate_binding():
    from app.assembly import assemble
    from app.models import SourceRef
    rows=[]
    for account,business,group in [('123','B1','G1'),('123','B1','G2'),('123','B2','G1'),('456','B1','G1')]:
        row=Requirement(id=account+business+group,account_id=account,business_no=business,name='Launch',
            sources={'name':SourceRef(file_id='f',sheet='S',cell='C2',raw_value='Launch')})
        rows.append((row,{'group_key':group,'budget_level':'campaign'},{}))
    plans=assemble(rows)
    assert len(plans)==3
    assert len(plans[0].structure.groups)==2
    assert not any('计划身份' in issue.message for issue in plans[0].issues)


def test_selecting_a_material_does_not_include_its_siblings():
    from app.scope import select_requirements
    from app.models import TaskScope, ScopeSelector
    reqs=parse('adgroup',('G1','G1','G2'),'300')
    scope=TaskScope(status='resolved',action='new',selectors=[ScopeSelector(field='post_id',values=['101'])],message_ids=['m'])
    selected=select_requirements(reqs,scope,[{'id':'m','role':'user','content':'只搭建帖子101'}])
    assert selected[0].included
    assert [a.post_id for g in selected[0].structure.groups for a in g.ads]==['101']
