from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
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


class Issue(Model):
    code: str
    field: str
    message: str
    blocking: bool = True


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


class SheetMapping(Model):
    sheet: str
    header_row: int = Field(ge=1)
    first_row: int = Field(ge=1)
    last_row: int = Field(ge=1)
    columns: dict[str, str]
    # Defaults are explicitly accepted by a user, not instructions extracted from cells.
    defaults: dict[str, str] = Field(default_factory=dict)


class MappingProposal(Model):
    mappings: list[SheetMapping]
    explanation: str


class DraftPatch(Model):
    expected_version: int
    requirement_id: str
    values: dict[str, Any]


class ChatEdit(Model):
    requirement_id: str
    values: dict[str, Any]


class ChatDecision(Model):
    answer: str
    edits: list[ChatEdit] = Field(default_factory=list)
