"""Merge reviewed task values with new file evidence without silently replacing them."""
from hashlib import sha256
from .models import Requirement, Issue


VALUE_FIELDS = tuple(field for field in Requirement.model_fields if field not in {
    'id','included','sources','confirmed_fields','classification','history','issues'})


def _missing(value):
    return value is None or value == ''


def _equal(left, right):
    if hasattr(left,'tzinfo') and hasattr(right,'tzinfo') and bool(left.tzinfo)!=bool(right.tzinfo):
        return False
    return left == right


def _complete_schedule(row):
    return bool(row.start_at and row.end_at and row.start_at.tzinfo and row.end_at.tzinfo)


def _same_schedule(left,right):
    return _complete_schedule(left) and _complete_schedule(right) and left.start_at==right.start_at and left.end_at==right.end_at


def _relation(left,right):
    """Return an exact link, an ambiguous link, or no relation between task records."""
    same_business=bool(left.business_no and left.business_no==right.business_no)
    same_identity=bool(left.account_id and left.account_id==right.account_id and left.post_id and left.post_id==right.post_id
                       and left.objective and left.objective==right.objective)
    if same_identity and _same_schedule(left,right):return 'exact'
    if left.id==right.id:return 'exact'
    if same_business:
        # The same business reference pointing at another post/account is a conflict.
        if any(not _missing(getattr(left,f)) and not _missing(getattr(right,f)) and not _equal(getattr(left,f),getattr(right,f))
               for f in ('account_id','post_id')):return 'conflict'
        # Explicit goals or batches are independent even for the same post/reference.
        if left.objective and right.objective and left.objective!=right.objective:return None
        if _complete_schedule(left) and _complete_schedule(right) and not _same_schedule(left,right):return None
        return 'exact'
    if same_identity:
        if _complete_schedule(left) and _complete_schedule(right):return None
        return 'ambiguous'
    return None


def _block(row,fields,reference):
    for field in fields:
        message=f'新输入与需求 {reference} 的 {field} 存在冲突或关联歧义，请核对原始证据后明确采用值与投放批次'
        issue=Issue(code='invalid_conflict',field=field,message=message)
        if not any(i.code==issue.code and i.field==field and i.message==message for i in row.issues):row.issues.append(issue)
    row.classification='incomplete'


def merge_requirements(existing: list[Requirement], incoming: list[Requirement]) -> list[Requirement]:
    """Fill missing values on a unique supported link; preserve conflicting evidence as unselected candidates.

    The caller holds task/draft versions and recalculates current task scope after merging.
    This function neither authorizes a scope nor executes or confirms a plan.
    """
    result=[r.model_copy(deep=True) for r in existing]
    for original in incoming:
        new=original.model_copy(deep=True)
        relations=[(index,_relation(row,new)) for index,row in enumerate(result)]
        related=[(index,kind) for index,kind in relations if kind]
        exact=[index for index,kind in related if kind=='exact']
        if len(related)==1 and len(exact)==1:
            index=exact[0];old=result[index]
            conflicts=[field for field in VALUE_FIELDS if not _missing(getattr(new,field)) and (
                _missing(getattr(old,field)) and field in old.confirmed_fields or
                not _missing(getattr(old,field)) and not _equal(getattr(old,field),getattr(new,field)))]
            # Shared business metadata may be absent in a prior message; fill it as evidence.
            if conflicts:
                _block(old,conflicts,new.id);_block(new,conflicts,old.id);new.included=False
            else:
                for field in VALUE_FIELDS:
                    if _missing(getattr(old,field)) and not _missing(getattr(new,field)):
                        setattr(old,field,getattr(new,field))
                        if field in new.sources:old.sources[field]=new.sources[field].model_copy(deep=True)
                    elif field not in old.sources and field in new.sources and _equal(getattr(old,field),getattr(new,field)):
                        old.sources[field]=new.sources[field].model_copy(deep=True)
                old.confirmed_fields=list(dict.fromkeys(old.confirmed_fields+[f for f in new.confirmed_fields if _equal(getattr(old,f),getattr(new,f))]))
                old.issues.extend(i.model_copy(deep=True) for i in new.issues if i not in old.issues)
                continue
        elif related:
            fields=['included']
            for index,_ in related:_block(result[index],fields,new.id)
            _block(new,fields,','.join(result[index].id for index,_ in related));new.included=False
        used={r.id for r in result}
        if new.id in used:
            new.id=sha256(('conflict:'+new.model_dump_json()).encode()).hexdigest()[:24]
        if new.id not in used:result.append(new)
    return result
