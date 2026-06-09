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

@router.post(
    "/upload",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a PDF syllabus for processing",
)
async def upload_document(
    course_id: str = Form(..., description="Identifier for the course/document"),
    file: UploadFile = File(..., description="The PDF syllabus to ingest"),
) -> IngestResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # Save file temporarily
    try:
        suffix = os.path.splitext(file.filename)[1]
        with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {exc}") from exc

    # Trigger async celery task
    task = process_pdf_task.delay(tmp_path, course_id)

    return IngestResponse(
        message="Background processing has started.",
        task_id=task.id,
        course_id=course_id,
    )
