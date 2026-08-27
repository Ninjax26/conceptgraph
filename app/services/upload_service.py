from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, func, select
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
        storage_key: str,
    ) -> DocumentUpload:
        now = datetime.now(timezone.utc)
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
            storage_key=storage_key,
            status="active",
            stage=ProcessingStage.UPLOADED.value,
            retryable=False,
            attempt_count=1,
            last_attempted_at=now,
            last_heartbeat_at=now,
        )
        session.add(record)
        session.add(
            ProcessingAttempt(
                id=str(uuid4()), document_id=upload_id, task_id=task_id,
                attempt_number=1, stage=ProcessingStage.UPLOADED.value,
                last_heartbeat_at=now,
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
        result = await session.execute(
            select(DocumentUpload)
            .where(
                DocumentUpload.status == "active",
                func.coalesce(
                    DocumentUpload.last_heartbeat_at,
                    DocumentUpload.updated_at,
                ) < cutoff,
            )
        )
        now = datetime.now(timezone.utc)
        for record in result.scalars().all():
            record.status = "failed"
            record.stage = ProcessingStage.FAILED.value
            record.failure_category = FailureCategory.WORKER_ERROR.value
            record.retryable = record.attempt_count < MAX_PROCESSING_ATTEMPTS
            record.error_message = "Processing was interrupted. Please retry this document."
            record.completed_at = now
            attempt = await self._current_attempt(session, record)
            if attempt is not None:
                attempt.stage = ProcessingStage.FAILED.value
                attempt.failure_category = FailureCategory.WORKER_ERROR.value
                attempt.retryable = record.retryable
                attempt.error_message = record.error_message
                attempt.completed_at = now
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
        record.last_heartbeat_at = record.last_attempted_at
        record.started_at = None
        record.completed_at = None
        session.add(
            ProcessingAttempt(
                id=str(uuid4()), document_id=record.upload_id, task_id=task_id,
                attempt_number=record.attempt_count, stage=ProcessingStage.UPLOADED.value,
                last_heartbeat_at=record.last_heartbeat_at,
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

    async def lock_failed_for_deletion(
        self,
        session: AsyncSession,
        upload_id: str,
    ) -> DocumentUpload | None:
        result = await session.execute(
            select(DocumentUpload)
            .where(DocumentUpload.upload_id == upload_id)
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None or record.stage != ProcessingStage.FAILED.value:
            return None
        return record

    async def storage_key_is_shared(
        self,
        session: AsyncSession,
        storage_key: str,
        excluding_upload_id: str,
    ) -> bool:
        result = await session.execute(
            select(func.count(DocumentUpload.upload_id)).where(
                DocumentUpload.storage_key == storage_key,
                DocumentUpload.upload_id != excluding_upload_id,
            )
        )
        return int(result.scalar_one()) > 0

    async def set_stage(
        self,
        session: AsyncSession,
        upload_id: str,
        task_id: str,
        stage: ProcessingStage,
    ) -> bool:
        record = await self._get_current_upload(session, upload_id, task_id)
        if record is None or record.status != "active":
            return False
        now = datetime.now(timezone.utc)
        record.stage = stage.value
        record.status = "active" if stage != ProcessingStage.READY else "ready"
        record.last_heartbeat_at = now
        if record.started_at is None:
            record.started_at = now
        attempt = await self._current_attempt(session, record)
        if attempt is not None:
            attempt.stage = stage.value
            if attempt.started_at is None:
                attempt.started_at = record.started_at
            attempt.last_heartbeat_at = now
        await session.commit()
        return True

    async def heartbeat(
        self,
        session: AsyncSession,
        upload_id: str,
        task_id: str,
    ) -> bool:
        record = await self._get_current_upload(session, upload_id, task_id)
        if record is None or record.status != "active":
            return False
        now = datetime.now(timezone.utc)
        record.last_heartbeat_at = now
        attempt = await self._current_attempt(session, record)
        if attempt is not None:
            attempt.last_heartbeat_at = now
        await session.commit()
        return True

    async def mark_completed(
        self,
        session: AsyncSession,
        upload_id: str,
        task_id: str,
        result_json: dict[str, Any],
    ) -> bool:
        record = await self._get_current_upload(session, upload_id, task_id)
        if record is None or record.status != "active":
            return False
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
        record.last_heartbeat_at = record.completed_at
        attempt = await self._current_attempt(session, record)
        if attempt is not None:
            attempt.stage = ProcessingStage.READY.value
            attempt.completed_at = record.completed_at
            attempt.last_heartbeat_at = record.completed_at
        await session.commit()
        return True

    async def mark_failed(
        self,
        session: AsyncSession,
        upload_id: str,
        task_id: str,
        error_message: str,
        category: FailureCategory = FailureCategory.UNKNOWN_ERROR,
        retryable: bool = False,
    ) -> bool:
        record = await self._get_current_upload(session, upload_id, task_id)
        if record is None or record.status != "active":
            return False
        record.status = "failed"
        record.stage = ProcessingStage.FAILED.value
        retry_limit_reached = (
            retryable and record.attempt_count >= MAX_PROCESSING_ATTEMPTS
        )
        safe_message = (
            "The retry limit was reached after repeated temporary failures. "
            "Remove this failed record and upload the PDF again."
            if retry_limit_reached
            else error_message
        )
        record.error_message = safe_message
        record.failure_category = category.value
        record.retryable = retryable and record.attempt_count < MAX_PROCESSING_ATTEMPTS
        record.completed_at = datetime.now(timezone.utc)
        record.last_heartbeat_at = record.completed_at
        attempt = await self._current_attempt(session, record)
        if attempt is not None:
            attempt.stage = ProcessingStage.FAILED.value
            attempt.failure_category = category.value
            attempt.retryable = record.retryable
            attempt.error_message = safe_message
            attempt.completed_at = record.completed_at
            attempt.last_heartbeat_at = record.completed_at
        await session.commit()
        return True

    async def _get_current_upload(
        self,
        session: AsyncSession,
        upload_id: str,
        task_id: str,
    ) -> DocumentUpload | None:
        result = await session.execute(
            select(DocumentUpload).where(
                DocumentUpload.upload_id == upload_id,
                DocumentUpload.task_id == task_id,
            )
        )
        return result.scalar_one_or_none()

    async def _current_attempt(
        self, session: AsyncSession, record: DocumentUpload
    ) -> ProcessingAttempt | None:
        result = await session.execute(
            select(ProcessingAttempt).where(ProcessingAttempt.task_id == record.task_id)
        )
        return result.scalar_one_or_none()
