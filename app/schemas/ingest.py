from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class IngestResponse(BaseModel):
    message: str
    task_id: str
    upload_id: str
    course_id: str
    week_number: int
    original_filename: str
    status: Literal["queued"] = "queued"
    preview_url: str


class UploadStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    upload_id: str
    task_id: str
    course_id: str
    week_number: int
    original_filename: str
    status: str
    error_message: str | None = None
    result_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    preview_url: str = Field(default="")
