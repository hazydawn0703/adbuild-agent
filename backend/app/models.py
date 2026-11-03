from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Annotated
from pydantic import BaseModel, Field, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Objective(StrEnum):
    FOLLOWERS = "followers"
    VIDEO_VIEWS = "video_views"
    TRAFFIC = "traffic"


class SourceRef(Model):
    file_id: str
    sheet: str
    cell: str
    raw_value: str
    kind: Literal["excel", "confirmed", "template", "history"] = "excel"
    message_id: str | None = None
    context_version: int | None = None
    accepted_by: str | None = None
    accepted_at: datetime | None = None


class Issue(Model):
    code: str
    field: str
    message: str
    blocking: bool = True


class BudgetSpec(Model):
    amount: Decimal | None = None
    allocation_ratio: Decimal | None = None
    currency: str = ''
    mode: Literal['lifetime', 'daily'] | None = None


class MaterialSpec(Model):
    id: str
    name: str = ''
    kind: Literal['post', 'authorization_code', 'unknown'] = 'post'
    post_id: str = ''
    post_url: str = ''
    identity_id: str = ''
    authorization_code: str = ''
    cta: str = ''
    landing_url: str = ''
    sources: dict[str, SourceRef] = Field(default_factory=dict)


class GroupSpec(Model):
    id: str
    name: str = ''
    audience: str | None = None
    language: str | None = None
    budget: BudgetSpec | None = None
    ads: list[MaterialSpec] = Field(default_factory=list, max_length=50)
    sources: dict[str, SourceRef] = Field(default_factory=dict)


class StructureSpec(Model):
    budget_level: Literal['campaign', 'adgroup'] = 'campaign'
    groups: list[GroupSpec] = Field(min_length=1, max_length=50)


class Requirement(Model):
    id: str
    included: bool = True
    account_id: str = ""
    business_no: str = ""
    name: str = ""
    post_id: str = ""
    post_url: str = ""
    objective: Objective | None = None
    budget: Decimal | None = None
    budget_mode: Literal["lifetime", "daily"] | None = None
    currency: str = ""
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str = ""
    audience: str = ""
    language: str = ""
    identity_id: str = ""
    cta: str = ""
    landing_url: str = ""
    sources: dict[str, SourceRef] = Field(default_factory=dict)
    confirmed_fields: list[str] = Field(default_factory=list)
    classification: Literal["new", "existing", "change", "incomplete", "ignored"] = "incomplete"
    history: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    structure: StructureSpec | None = None


class BuildApproval(Model):
    version: int
    digest: str
    approved_by: str
    approved_at: datetime
    snapshot: dict[str, Any]


class BuildDraft(Model):
    id: str
    project_id: str
    version: int = 1
    status: Literal["mapping", "draft", "approved", "queued", "executing", "complete", "attention"] = "draft"
    approval: BuildApproval | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class ProjectPolicy(Model):
    version: int = 1
    enabled: bool = False
    account_ids: list[str]
    objectives: list[Objective]
    audiences: list[str]
    identities: list[str]
    currency: str = Field(pattern="^[A-Z]{3}$")
    per_item_limit: Decimal = Field(gt=0)
    batch_limit: Decimal = Field(gt=0)
    cumulative_limit: Decimal = Field(gt=0)
    valid_until: datetime


InputField = Literal['account_id','business_no','name','post_url','post_id','objective','budget','budget_mode','currency','start_at','end_at','timezone','audience','language','identity_id','cta','landing_url']


class Binding(Model):
    kind: Literal['row','cell','header']
    column: str = ''
    sheet: str = ''
    cell: str = ''
    transform: Literal['value','interval_start','interval_end','currency_code'] = 'value'


class RowFilter(Model):
    column: str
    operator: Literal['eq','in','nonempty']
    values: list[str] = Field(default_factory=list)


class SheetMapping(Model):
    sheet: str
    header_row: int = Field(ge=1)
    first_row: int = Field(ge=1)
    last_row: int = Field(ge=1)
    columns: dict[InputField, Annotated[str, Field(pattern=r'^[A-Z]{1,3}$')]] = Field(default_factory=dict)
    bindings: dict[InputField, Binding] = Field(default_factory=dict)
    filters: list[RowFilter] = Field(default_factory=list)
    # Defaults are explicitly accepted by a user, not instructions extracted from cells.
    defaults: dict[InputField, str] = Field(default_factory=dict)


class MappingProposal(Model):
    mappings: list[SheetMapping]
    explanation: str
    complete: bool = True
    pending_ranges: list["RangeRequest"] = Field(default_factory=list)
    regions: list['RegionProposal'] = Field(default_factory=list)
    requested_ranges: list['RangeRequest'] = Field(default_factory=list)


class DraftPatch(Model):
    expected_version: int
    edits: list["ChatEdit"]


class ChatEdit(Model):
    requirement_id: str
    values: dict[InputField | Literal['included','structure'], Any]
    sources: dict[str, SourceRef] = Field(default_factory=dict)


class ScopeSelector(Model):
    field: Literal['business_no','post_id','name','platform','objective']
    values: list[str]


class TaskScope(Model):
    action: Literal['new','repost','edit','exclude','history','unknown'] = 'unknown'
    status: Literal['unresolved','resolved'] = 'unresolved'
    selectors: list[ScopeSelector] = Field(default_factory=list)
    exclude_selectors: list[ScopeSelector] = Field(default_factory=list)
    all_records: bool = False
    message_ids: list[str] = Field(default_factory=list)
    reason: str = ''


class RequirementExpansion(Model):
    base_requirement_id: str
    objective: Objective
    budget: Decimal = Field(gt=0)


class ChatDecision(Model):
    answer: str
    scope: TaskScope | None = None
    expansions: list[RequirementExpansion] = Field(default_factory=list)
    edits: list[ChatEdit] = Field(default_factory=list)
    questions: list['Clarification'] = Field(default_factory=list)


class RegionProposal(Model):
    sheet: str
    range: str
    role: Literal['requirements','history','instructions','account','statistics','unknown']
    reason: str


class RangeRequest(Model):
    sheet: str
    range: str
    reason: str


class EvidenceRef(Model):
    kind: Literal['excel','history','message','template']
    ref_id: str
    detail: dict[str, Any] = Field(default_factory=dict)


class FieldEvidence(Model):
    requirement_id: str
    field: str
    state: Literal['explicit','inferred','conflict','missing']
    sources: list[EvidenceRef] = Field(default_factory=list)
    reason: str = ''


class Clarification(Model):
    id: str
    field: InputField | Literal['included','scope','structure']
    scope: Literal['task','requirements'] = 'requirements'
    requirement_ids: list[str] = Field(default_factory=list)
    question: str
    answer_type: Literal['text','choice','datetime','decimal'] = 'text'
    options: list[str] = Field(default_factory=list)
    blocking: bool = True


class ChangeProposal(Model):
    id: str
    draft_version: int
    expansions: list[RequirementExpansion] = Field(default_factory=list)
    edits: list[ChatEdit]
    reason: str
    status: Literal['pending','accepted','rejected'] = 'pending'
    message_id: str | None = None


class PlatformCheck(Model):
    item: str
    status: Literal['unverified','passed','failed','not_applicable'] = 'unverified'
    reason: str = '尚未连接后台核实'


class ProjectContext(Model):
    version: int = 0
    defaults: dict[InputField, str] = Field(default_factory=dict)
    account_ids: list[str] = Field(default_factory=list)
    objectives: list[Objective] = Field(default_factory=list)
    terms: dict[str, str] = Field(default_factory=dict)
    confirmed_by: str = ''


class IntakeTask(Model):
    id: str
    project_id: str
    version: int = 1
    name: str = '新搭建任务'
    draft_id: str | None = None
    file_ids: list[str] = Field(default_factory=list)
    scope: TaskScope = Field(default_factory=TaskScope)
    answers: dict[str, dict] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    stage: Literal['intake','mapping','clarifying','preview','attention'] = 'intake'
    messages: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[FieldEvidence] = Field(default_factory=list)
    questions: list[Clarification] = Field(default_factory=list)
    semantic_questions: list[Clarification] = Field(default_factory=list)
    proposal: ChangeProposal | None = None
    context: ProjectContext = Field(default_factory=ProjectContext)
    requests: dict[str, dict[str, Any]] = Field(default_factory=dict)
    lease: dict[str, Any] | None = None


MappingProposal.model_rebuild()

DraftPatch.model_rebuild()
ChatDecision.model_rebuild()
