"""API endpoint for document ingestion."""

import os
import shutil
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.tasks.document_tasks import process_pdf_task

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

class IngestResponse(BaseModel):
    message: str
    task_id: str
    course_id: str
    week_number: int

@router.post(
    "/upload",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a PDF syllabus for processing",
)
async def upload_document(
    course_id: str = Form(..., description="Identifier for the course/document"),
    week_number: int = Form(1, description="Syllabus week number for exam filtering"),
    file: UploadFile = File(..., description="The PDF syllabus to ingest"),
) -> IngestResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    if week_number < 1:
        raise HTTPException(status_code=400, detail="week_number must be 1 or greater.")

    # Save file temporarily
    try:
        suffix = os.path.splitext(file.filename)[1]
        with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {exc}") from exc

    # Trigger async celery task
    task = process_pdf_task.delay(tmp_path, course_id, week_number)

    return IngestResponse(
        message="Background processing has started.",
        task_id=task.id,
        course_id=course_id,
        week_number=week_number,
    )
