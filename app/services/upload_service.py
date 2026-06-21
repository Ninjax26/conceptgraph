from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_upload import DocumentUpload


class UploadService:
    async def create_upload(
        self,
        session: AsyncSession,
        *,
        upload_id: str,
        task_id: str,
        course_id: str,
        week_number: int,
        original_filename: str,
        stored_file_path: str,
    ) -> DocumentUpload:
        record = DocumentUpload(
            upload_id=upload_id,
            task_id=task_id,
            course_id=course_id,
            week_number=week_number,
            original_filename=original_filename,
            stored_file_path=stored_file_path,
            status="queued",
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record

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

    async def mark_running(
        self,
        session: AsyncSession,
        upload_id: str,
    ) -> None:
        record = await self.get_upload(session, upload_id)
        if record is None:
            return
        record.status = "running"
        record.started_at = datetime.now(timezone.utc)
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
        record.status = "completed"
        record.result_json = result_json
        record.completed_at = datetime.now(timezone.utc)
        record.error_message = None
        await session.commit()

    async def mark_failed(
        self,
        session: AsyncSession,
        upload_id: str,
        error_message: str,
    ) -> None:
        record = await self.get_upload(session, upload_id)
        if record is None:
            return
        record.status = "failed"
        record.error_message = error_message
        record.completed_at = datetime.now(timezone.utc)
        await session.commit()
