from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.processing import FailureCategory, MAX_PROCESSING_ATTEMPTS, ProcessingStage
from app.models.document_upload import Course, DocumentUpload, ProcessingAttempt


class UploadService:
    async def create_upload(
        self,
        session: AsyncSession,
        *,
        upload_id: str,
        task_id: str,
        course: Course,
        content_hash: str,
        original_filename: str,
        stored_file_path: str,
    ) -> DocumentUpload:
        record = DocumentUpload(
            upload_id=upload_id,
            task_id=task_id,
            course_id=course.display_name,
            course_uuid=course.id,
            content_hash=content_hash,
            # Kept internally until the database schema is migrated; it is no longer
            # part of the product or retrieval model.
            week_number=1,
            original_filename=original_filename,
            stored_file_path=stored_file_path,
            status="active",
            stage=ProcessingStage.UPLOADED.value,
            retryable=False,
            attempt_count=1,
            last_attempted_at=datetime.now(timezone.utc),
        )
        session.add(record)
        session.add(
            ProcessingAttempt(
                id=str(uuid4()), document_id=upload_id, task_id=task_id,
                attempt_number=1, stage=ProcessingStage.UPLOADED.value,
            )
        )
        await session.commit()
        await session.refresh(record)
        return record

    async def find_duplicate(
        self, session: AsyncSession, course_uuid: str, content_hash: str
    ) -> DocumentUpload | None:
        result = await session.execute(
            select(DocumentUpload)
            .where(
                DocumentUpload.course_uuid == course_uuid,
                DocumentUpload.content_hash == content_hash,
            )
            .order_by(desc(DocumentUpload.created_at))
        )
        return result.scalars().first()

    async def get_upload(self, session: AsyncSession, upload_id: str) -> DocumentUpload | None:
        result = await session.execute(
            select(DocumentUpload).where(DocumentUpload.upload_id == upload_id)
        )
        return result.scalar_one_or_none()

    async def get_upload_by_task_id(
        self,
        session: AsyncSession,
        task_id: str,
    ) -> DocumentUpload | None:
        result = await session.execute(
            select(DocumentUpload).where(DocumentUpload.task_id == task_id)
        )
        return result.scalar_one_or_none()

    async def list_uploads(
        self,
        session: AsyncSession,
        limit: int = 25,
    ) -> list[DocumentUpload]:
        result = await session.execute(
            select(DocumentUpload).order_by(desc(DocumentUpload.created_at)).limit(limit)
        )
        return list(result.scalars().all())

    async def expire_stale_uploads(self, session: AsyncSession) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        await session.execute(
            update(DocumentUpload)
            .where(
                DocumentUpload.status == "active",
                DocumentUpload.updated_at < cutoff,
            )
            .values(
                status="failed",
                stage=ProcessingStage.FAILED.value,
                failure_category=FailureCategory.WORKER_ERROR.value,
                retryable=DocumentUpload.attempt_count < MAX_PROCESSING_ATTEMPTS,
                error_message="Processing was interrupted. Please retry this document.",
                completed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    async def retry_upload(
        self,
        session: AsyncSession,
        upload_id: str,
        task_id: str,
    ) -> DocumentUpload | None:
        result = await session.execute(
            select(DocumentUpload)
            .where(DocumentUpload.upload_id == upload_id)
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if (
            record is None
            or record.stage != ProcessingStage.FAILED.value
            or not record.retryable
            or record.attempt_count >= MAX_PROCESSING_ATTEMPTS
        ):
            return None
        record.task_id = task_id
        record.status = "active"
        record.stage = ProcessingStage.UPLOADED.value
        record.error_message = None
        record.result_json = None
        record.failure_category = None
        record.retryable = False
        record.attempt_count += 1
        record.last_attempted_at = datetime.now(timezone.utc)
        record.started_at = None
        record.completed_at = None
        session.add(
            ProcessingAttempt(
                id=str(uuid4()), document_id=record.upload_id, task_id=task_id,
                attempt_number=record.attempt_count, stage=ProcessingStage.UPLOADED.value,
            )
        )
        await session.commit()
        await session.refresh(record)
        return record

    async def delete_failed(self, session: AsyncSession, upload_id: str) -> DocumentUpload | None:
        record = await self.get_upload(session, upload_id)
        if record is None or record.stage != ProcessingStage.FAILED.value:
            return None
        await session.delete(record)
        await session.commit()
        return record

    async def set_stage(
        self,
        session: AsyncSession,
        upload_id: str,
        stage: ProcessingStage,
    ) -> None:
        record = await self.get_upload(session, upload_id)
        if record is None:
            return
        record.stage = stage.value
        record.status = "active" if stage != ProcessingStage.READY else "ready"
        if record.started_at is None:
            record.started_at = datetime.now(timezone.utc)
        attempt = await self._current_attempt(session, record)
        if attempt is not None:
            attempt.stage = stage.value
            if attempt.started_at is None:
                attempt.started_at = record.started_at
        await session.commit()

    async def mark_completed(
        self,
        session: AsyncSession,
        upload_id: str,
        result_json: dict[str, Any],
    ) -> None:
        record = await self.get_upload(session, upload_id)
        if record is None:
            return
        record.status = "ready"
        record.stage = ProcessingStage.READY.value
        record.result_json = result_json
        record.processed_chunk_count = int(result_json.get("chunks_indexed", 0))
        record.graph_node_count = int(result_json.get("nodes_upserted", 0))
        record.graph_edge_count = int(result_json.get("relationships_upserted", 0))
        record.completed_at = datetime.now(timezone.utc)
        record.error_message = None
        record.failure_category = None
        record.retryable = False
        attempt = await self._current_attempt(session, record)
        if attempt is not None:
            attempt.stage = ProcessingStage.READY.value
            attempt.completed_at = record.completed_at
        await session.commit()

    async def mark_failed(
        self,
        session: AsyncSession,
        upload_id: str,
        error_message: str,
        category: FailureCategory = FailureCategory.UNKNOWN_ERROR,
        retryable: bool = False,
    ) -> None:
        record = await self.get_upload(session, upload_id)
        if record is None:
            return
        record.status = "failed"
        record.stage = ProcessingStage.FAILED.value
        record.error_message = error_message
        record.failure_category = category.value
        record.retryable = retryable and record.attempt_count < MAX_PROCESSING_ATTEMPTS
        record.completed_at = datetime.now(timezone.utc)
        attempt = await self._current_attempt(session, record)
        if attempt is not None:
            attempt.stage = ProcessingStage.FAILED.value
            attempt.failure_category = category.value
            attempt.retryable = record.retryable
            attempt.error_message = error_message
            attempt.completed_at = record.completed_at
        await session.commit()

    async def _current_attempt(
        self, session: AsyncSession, record: DocumentUpload
    ) -> ProcessingAttempt | None:
        result = await session.execute(
            select(ProcessingAttempt).where(ProcessingAttempt.task_id == record.task_id)
        )
        return result.scalar_one_or_none()
