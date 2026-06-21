"""API endpoints for document ingestion and upload tracking."""

from pathlib import Path
from uuid import uuid4

import shutil
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_postgres_session
from app.schemas.ingest import IngestResponse, UploadStatusResponse
from app.services.upload_service import UploadService
from app.tasks.document_tasks import process_pdf_task

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

upload_service = UploadService()

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
    db: AsyncSession = Depends(get_postgres_session),
) -> IngestResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    if week_number < 1:
        raise HTTPException(status_code=400, detail="week_number must be 1 or greater.")

    upload_id = str(uuid4())
    task_id = upload_id
    stored_file_path = UPLOAD_DIR / f"{upload_id}.pdf"

    try:
        with stored_file_path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {exc}") from exc

    try:
        await upload_service.create_upload(
            db,
            upload_id=upload_id,
            task_id=task_id,
            course_id=course_id,
            week_number=week_number,
            original_filename=file.filename,
            stored_file_path=str(stored_file_path),
        )
    except Exception as exc:
        if stored_file_path.exists():
            stored_file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to track upload: {exc}") from exc

    task = process_pdf_task.apply_async(
        args=[upload_id, str(stored_file_path), course_id, week_number],
        task_id=task_id,
    )

    return IngestResponse(
        message="Background processing has started.",
        task_id=task.id,
        upload_id=upload_id,
        course_id=course_id,
        week_number=week_number,
        original_filename=file.filename,
        preview_url=f"/api/v1/ingest/uploads/{upload_id}/preview",
    )


@router.get("/status/{task_id}", response_model=UploadStatusResponse)
async def get_upload_status(
    task_id: str,
    db: AsyncSession = Depends(get_postgres_session),
) -> UploadStatusResponse:
    record = await upload_service.get_upload_by_task_id(db, task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Upload not found.")

    return UploadStatusResponse(
        upload_id=record.upload_id,
        task_id=record.task_id,
        course_id=record.course_id,
        week_number=record.week_number,
        original_filename=record.original_filename,
        status=record.status,
        error_message=record.error_message,
        result_json=record.result_json,
        created_at=record.created_at,
        updated_at=record.updated_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        preview_url=f"/api/v1/ingest/uploads/{record.upload_id}/preview",
    )


@router.get("/uploads/{upload_id}/preview")
async def preview_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_postgres_session),
) -> FileResponse:
    record = await upload_service.get_upload(db, upload_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Upload not found.")

    pdf_path = Path(record.stored_file_path)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Stored PDF is no longer available.")

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=record.original_filename,
    )
