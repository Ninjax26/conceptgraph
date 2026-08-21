"""API endpoints for document ingestion and upload tracking."""

from pathlib import Path
from uuid import uuid4
import hashlib

import shutil
import fitz
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.database import get_postgres_session
from app.schemas.ingest import CourseSummaryResponse, IngestResponse, UploadStatusResponse
from app.services.upload_service import UploadService
from app.services.course_service import CourseService
from app.core.processing import normalize_course_name
from app.core.processing import FailureCategory
from app.tasks.document_tasks import process_pdf_task

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

upload_service = UploadService()
course_service = CourseService()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

@router.post(
    "/upload",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a PDF syllabus for processing",
)
async def upload_document(
    course_id: str = Form(..., description="Identifier for the course/document"),
    file: UploadFile = File(..., description="The PDF syllabus to ingest"),
    db: AsyncSession = Depends(get_postgres_session),
) -> IngestResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    if file.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(status_code=400, detail="The selected file is not a valid PDF.")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF files must be 10 MB or smaller.")
    course_id = course_id.strip()
    if not course_id:
        raise HTTPException(status_code=400, detail="course_id cannot be empty.")

    upload_id = str(uuid4())
    task_id = str(uuid4())
    stored_file_path = UPLOAD_DIR / f"{upload_id}.pdf"

    try:
        with stored_file_path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination)
        if stored_file_path.stat().st_size > MAX_UPLOAD_BYTES:
            stored_file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=413, detail="PDF files must be 10 MB or smaller.")
        with fitz.open(stored_file_path) as document:
            if document.needs_pass:
                raise HTTPException(
                    status_code=400,
                    detail="Password-protected PDFs are not supported.",
                )
            if document.page_count == 0:
                raise HTTPException(status_code=400, detail="The PDF contains no pages.")
    except HTTPException:
        stored_file_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        stored_file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The PDF is malformed or unreadable.") from exc

    content_hash = _sha256_file(stored_file_path)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"course:{normalize_course_name(course_id)}"},
    )
    course = await course_service.get_or_create(db, course_id)
    duplicate = await upload_service.find_duplicate(db, course.id, content_hash)
    if duplicate is not None:
        stored_file_path.unlink(missing_ok=True)
        return _ingest_response(
            duplicate,
            message="This PDF already exists in the course. The existing document was returned.",
            duplicate=True,
        )

    try:
        await upload_service.create_upload(
            db,
            upload_id=upload_id,
            task_id=task_id,
            course=course,
            content_hash=content_hash,
            original_filename=file.filename,
            stored_file_path=str(stored_file_path),
        )
    except Exception as exc:
        if stored_file_path.exists():
            stored_file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="Document tracking is temporarily unavailable.") from exc

    try:
        task = process_pdf_task.apply_async(
            args=[upload_id, str(stored_file_path), course.id, course.display_name, file.filename],
            task_id=task_id,
        )
    except Exception as exc:
        await upload_service.mark_failed(
            db,
            upload_id,
            task_id,
            "The processing worker is unavailable. Please retry when the service is restored.",
            FailureCategory.WORKER_ERROR,
            True,
        )
        raise HTTPException(
            status_code=503,
            detail="The processing worker is unavailable. The upload was saved and can be retried.",
        ) from exc

    return IngestResponse(
        message="Background processing has started.",
        task_id=task.id,
        upload_id=upload_id,
        course_id=course.id,
        course_name=course.display_name,
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
        course_id=record.course_uuid or record.course_id,
        original_filename=record.original_filename,
        course_name=record.course_id,
        status=record.status,
        stage=record.stage,
        failure_category=record.failure_category,
        retryable=record.retryable,
        attempt_count=record.attempt_count,
        last_attempted_at=record.last_attempted_at,
        last_heartbeat_at=record.last_heartbeat_at,
        processed_chunk_count=record.processed_chunk_count,
        graph_node_count=record.graph_node_count,
        graph_edge_count=record.graph_edge_count,
        error_message=record.error_message,
        result_json=record.result_json,
        created_at=record.created_at,
        updated_at=record.updated_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        preview_url=f"/api/v1/ingest/uploads/{record.upload_id}/preview",
    )


@router.get("/uploads", response_model=list[UploadStatusResponse])
async def list_uploads(
    limit: int = 25,
    db: AsyncSession = Depends(get_postgres_session),
) -> list[UploadStatusResponse]:
    await upload_service.expire_stale_uploads(db)
    records = await upload_service.list_uploads(db, limit=100)
    grouped: dict[tuple[str, str], object] = {}
    rank = {"active": 0, "ready": 1, "failed": 2, "cancelled": 3}
    for record in records:
        key = (record.course_uuid or record.course_id, record.content_hash or record.upload_id)
        current = grouped.get(key)
        if current is None or rank.get(record.status, 9) < rank.get(current.status, 9):
            grouped[key] = record
    records = list(grouped.values())[: max(1, min(limit, 100))]
    return [
        UploadStatusResponse(
            upload_id=record.upload_id,
            task_id=record.task_id,
            course_id=record.course_uuid or record.course_id,
            course_name=record.course_id,
            original_filename=record.original_filename,
            status=record.status,
            stage=record.stage,
            failure_category=record.failure_category,
            retryable=record.retryable,
            attempt_count=record.attempt_count,
            last_attempted_at=record.last_attempted_at,
            last_heartbeat_at=record.last_heartbeat_at,
            processed_chunk_count=record.processed_chunk_count,
            graph_node_count=record.graph_node_count,
            graph_edge_count=record.graph_edge_count,
            error_message=record.error_message,
            result_json=record.result_json,
            created_at=record.created_at,
            updated_at=record.updated_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            preview_url=f"/api/v1/ingest/uploads/{record.upload_id}/preview",
        )
        for record in records
    ]


@router.get("/courses", response_model=list[CourseSummaryResponse])
async def list_courses(
    db: AsyncSession = Depends(get_postgres_session),
) -> list[CourseSummaryResponse]:
    summaries = await course_service.list_summaries(db)
    return [
        CourseSummaryResponse(
            course_id=summary.course.id,
            course_name=summary.course.display_name,
            total_documents=summary.total_documents,
            active_documents=summary.active_documents,
            ready_documents=summary.ready_documents,
            failed_documents=summary.failed_documents,
            processed_chunk_count=summary.processed_chunk_count,
            graph_node_count=summary.graph_node_count,
            graph_edge_count=summary.graph_edge_count,
            last_updated_at=summary.last_updated_at,
            historical_records=summary.historical_records,
            duplicate_records=summary.duplicate_records,
        )
        for summary in summaries
    ]


@router.post("/uploads/{upload_id}/retry", response_model=IngestResponse)
async def retry_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_postgres_session),
) -> IngestResponse:
    existing = await upload_service.get_upload(db, upload_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Upload not found.")
    if existing.stage != "FAILED" or not existing.retryable:
        raise HTTPException(status_code=409, detail="Only failed uploads can be retried.")

    task_id = str(uuid4())
    record = await upload_service.retry_upload(db, upload_id, task_id)
    if record is None:
        raise HTTPException(status_code=409, detail="Only failed uploads can be retried.")

    pdf_path = Path(record.stored_file_path)
    if not pdf_path.exists():
        await upload_service.mark_failed(
            db,
            upload_id,
            task_id,
            "The stored PDF is no longer available. Upload the document again.",
            FailureCategory.DOCUMENT_ERROR,
            False,
        )
        raise HTTPException(status_code=404, detail="The stored PDF is no longer available.")

    try:
        task = process_pdf_task.apply_async(
            args=[
                record.upload_id,
                record.stored_file_path,
                record.course_uuid,
                record.course_id,
                record.original_filename,
            ],
            task_id=task_id,
        )
    except Exception as exc:
        await upload_service.mark_failed(
            db,
            upload_id,
            task_id,
            "The processing worker is unavailable. Please retry when the service is restored.",
            FailureCategory.WORKER_ERROR,
            True,
        )
        raise HTTPException(
            status_code=503,
            detail="The processing worker is unavailable. The retry was saved and can be attempted again.",
        ) from exc
    return IngestResponse(
        message="Document processing has been queued again.",
        task_id=task.id,
        upload_id=record.upload_id,
        course_id=record.course_uuid or record.course_id,
        course_name=record.course_id,
        original_filename=record.original_filename,
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


@router.delete("/uploads/{upload_id}", status_code=204)
async def remove_failed_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_postgres_session),
) -> None:
    record = await upload_service.delete_failed(db, upload_id)
    if record is None:
        raise HTTPException(status_code=409, detail="Only failed document records can be removed.")
    Path(record.stored_file_path).unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ingest_response(record, *, message: str, duplicate: bool) -> IngestResponse:
    return IngestResponse(
        message=message,
        task_id=record.task_id,
        upload_id=record.upload_id,
        course_id=record.course_uuid or record.course_id,
        course_name=record.course_id,
        original_filename=record.original_filename,
        status=record.stage,
        duplicate=duplicate,
        preview_url=f"/api/v1/ingest/uploads/{record.upload_id}/preview",
    )
