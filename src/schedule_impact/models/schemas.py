"""Pydantic models for config and pipeline artefacts — expand during Phase 1."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class IncidentType(str, Enum):
    IMPACT = "impact"
    FLOAT = "float"


class Incident(BaseModel):
    incident_id: str
    incident_type: IncidentType
    project_row_id: str
    reporting_period: str
    previous_period: str | None = None
    primary_task_id: str | None = None
    delay_days: float
    delay_metric: str = "finish_slip_working_days"
    cp_effect_days: float | None = None


class NarrativeChunk(BaseModel):
    chunk_id: str
    document_id: str
    reporting_period: str
    text: str
    source: Literal["xer_taskmemo", "pdf"] = "pdf"
    project_row_id: str | None = None
    section_title: str | None = None
    task_id: str | None = None
    task_code: str | None = None
    memo_type_label: str | None = None


class IncidentNarrativeLink(BaseModel):
    incident_id: str
    chunk_id: str
    link_method: Literal[
        "explicit_id", "wbs_match", "code_match", "temporal", "fuzzy", "llm", "manual"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = None


class QualityAssessment(BaseModel):
    incident_id: str
    is_quality_related: bool
    quality_confidence: float = Field(ge=0.0, le=1.0)
    classifier: Literal["keywords", "llm", "hybrid", "ml_v1"]
    review_status: Literal["auto", "needs_review", "confirmed", "rejected"] = "auto"
