from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Annotated
from pydantic import BaseModel, Field, ConfigDict, computed_field


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
    kind: Literal["excel", "confirmed", "template", "history", "project", "calculated"] = "excel"
    derived_from: list[str] = Field(default_factory=list)
    message_id: str | None = None
    context_version: int | None = None
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    transform: Literal['value','interval_start','interval_end','currency_code'] = 'value'


class Issue(Model):
    code: str
    field: str
    message: str
    blocking: bool = True
    target_id: str = ''


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
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str = ''
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
    platform: str = ''
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
    protocol_version: Literal[3] = 3
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


InputField = Literal['platform','account_id','business_no','name','post_url','post_id','objective','budget','budget_mode','currency','start_at','end_at','timezone','audience','language','identity_id','cta','landing_url']
MappingField = Literal['platform','account_id','business_no','name','post_url','post_id','objective','budget','budget_mode','currency','start_at','end_at','timezone','audience','language','identity_id','cta','landing_url','campaign_key','group_key','record_key','budget_level','campaign_budget','adgroup_budget']


class Binding(Model):
    kind: Literal['row','cell','header']
    column: str = Field(default='',pattern=r'^(?:[A-Z]{1,3})?$')
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
    bindings: dict[MappingField, Binding] = Field(default_factory=dict,description='实际来源绑定，包括实体关系。campaign_key=计划身份，group_key=广告组身份，record_key=素材记录身份，budget_level=预算层级，campaign_budget=计划总额，adgroup_budget=组预算；识别到来源就必须实际输出这些键，不能只在explanation描述。每个键只有一种row/cell/header来源。')
    filters: list[RowFilter] = Field(default_factory=list)


class MappingProposal(Model):
    mappings: list[SheetMapping]
    explanation: str
    complete: bool = True
    pending_ranges: list["RangeRequest"] = Field(default_factory=list)
    read_state: dict[str, Any] = Field(default_factory=dict)
    regions: list['RegionProposal'] = Field(default_factory=list)
    requested_ranges: list['RangeRequest'] = Field(default_factory=list)

    @computed_field
    @property
    def status(self) -> Literal['ready','needs_input','incomplete']:
        if not self.complete or self.pending_ranges or self.requested_ranges:return 'incomplete'
        return 'ready' if self.mappings else 'needs_input'


class DraftPatch(Model):
    expected_version: int
    edits: list["ChatEdit"]


class ChatEdit(Model):
    requirement_id: str
    target_id: str = ''
    values: dict[InputField | Literal['included','structure'], Any]
    sources: dict[str, SourceRef] = Field(default_factory=dict)


class ScopeSelector(Model):
    field: Literal['requirement_id','business_no','post_id','name','platform','objective']
    values: list[str]


class TaskScope(Model):
    action: Literal['new','repost','edit','exclude','history','unknown'] = 'unknown'
    status: Literal['unresolved','resolved'] = 'unresolved'
    selectors: list[ScopeSelector] = Field(default_factory=list)
    exclude_selectors: list[ScopeSelector] = Field(default_factory=list)
    all_records: bool = False
    message_ids: list[str] = Field(default_factory=list)
    reason: str = ''


class DemandCandidate(Model):
    values: dict[InputField, Any]
    sources: dict[str, SourceRef] = Field(default_factory=dict)
    structure: StructureSpec | None = None


class RequirementExpansion(Model):
    base_requirement_id: str
    objective: Objective
    budget: Decimal = Field(gt=0)


class ChatDecision(Model):
    answer: str
    expansions: list[RequirementExpansion] = Field(default_factory=list)
    additions: list[DemandCandidate] = Field(default_factory=list)
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
    reason_code: Literal['semantic_review','missing','conflict','scope','unsupported','platform_check'] = Field(default='semantic_review',description='平台账户/受众/Spark权限核实属于platform_check，仅用于执行前核验，不阻断草稿预览；不能把授权码是否存在当作帖子权限结论。')
    evidence_version: int = 0
    target_ids: list[str] = Field(default_factory=list)


class TaskQuestion(Clarification):
    scope: Literal['task'] = 'task'
    requirement_ids: list[str] = Field(default_factory=list, max_length=0)


class CandidateDecision(Model):
    answer: str
    additions: list[DemandCandidate] = Field(default_factory=list)
    questions: list[TaskQuestion] = Field(default_factory=list)


class DraftDecision(Model):
    answer: str
    edits: list[ChatEdit] = Field(default_factory=list)
    expansions: list[RequirementExpansion] = Field(default_factory=list)
    questions: list[Clarification] = Field(default_factory=list)


class ChangeProposal(Model):
    id: str
    draft_version: int
    expansions: list[RequirementExpansion] = Field(default_factory=list)
    additions: list[DemandCandidate] = Field(default_factory=list)
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
    archived_at: datetime | None = None
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

