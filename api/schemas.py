"""Pydantic request/response models for the public REST API."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    db: bool
    openai_configured: bool
    index_size: int


class StatsResponse(BaseModel):
    users: int
    questions: int
    questions_last_week: int
    unanswered: int
    unanswered_pct: float
    index_size: int
    feedback_up: int
    feedback_down: int
    top_unanswered: list[str] = []
    cost_note: str = "See /metrics + Grafana for cost"


class DocumentItem(BaseModel):
    id: int
    filename: str
    chunk_count: int
    uploaded_at: str


class DocumentsResponse(BaseModel):
    documents: list[DocumentItem]


class UploadResponse(BaseModel):
    document_id: int
    filename: str
    chunks_created: int
    total_index_size: int


class ErrorResponse(BaseModel):
    detail: str
