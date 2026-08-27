import argparse
import asyncio
from datetime import datetime, timezone

from qdrant_client.models import FieldCondition, Filter, MatchValue
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, qdrant_client
from app.core.processing import FailureCategory, MAX_PROCESSING_ATTEMPTS, ProcessingStage
from app.models.document_upload import DocumentUpload, ProcessingAttempt
from app.services.storage_service import storage_service


async def reconcile(*, apply: bool) -> list[dict[str, object]]:
    reconciled: list[dict[str, object]] = []
    async with AsyncSessionLocal() as session:
        records = list(
            (
                await session.execute(
                    select(DocumentUpload)
                    .where(DocumentUpload.stage == ProcessingStage.READY.value)
                    .with_for_update()
                )
            ).scalars()
        )

        for record in records:
            vector_count = qdrant_client.count(
                collection_name=settings.qdrant_collection_name,
                count_filter=Filter(
                    must=[
                        FieldCondition(
                            key="upload_id",
                            match=MatchValue(value=record.upload_id),
                        )
                    ]
                ),
                exact=True,
            ).count
            if vector_count > 0:
                continue

            source_available = await asyncio.to_thread(
                storage_service.exists,
                record.storage_key,
            )
            retryable = source_available and record.attempt_count < MAX_PROCESSING_ATTEMPTS
            category = (
                FailureCategory.DATABASE_ERROR
                if source_available
                else FailureCategory.DOCUMENT_ERROR
            )
            message = (
                "The vector index is missing after a storage migration. Reprocess this document."
                if source_available
                else "The vector index and source PDF are unavailable. Upload the document again."
            )
            reconciled.append(
                {
                    "upload_id": record.upload_id,
                    "course": record.course_id,
                    "document": record.original_filename,
                    "retryable": retryable,
                }
            )
            if not apply:
                continue

            now = datetime.now(timezone.utc)
            record.status = "failed"
            record.stage = ProcessingStage.FAILED.value
            record.failure_category = category.value
            record.retryable = retryable
            record.error_message = message
            record.processed_chunk_count = 0
            record.result_json = None
            record.completed_at = now
            record.last_heartbeat_at = now
            attempt = (
                await session.execute(
                    select(ProcessingAttempt).where(
                        ProcessingAttempt.document_id == record.upload_id,
                        ProcessingAttempt.task_id == record.task_id,
                    )
                )
            ).scalar_one_or_none()
            if attempt is not None:
                attempt.stage = ProcessingStage.FAILED.value
                attempt.failure_category = category.value
                attempt.retryable = retryable
                attempt.error_message = message
                attempt.completed_at = now
                attempt.last_heartbeat_at = now

        if apply:
            await session.commit()
        else:
            await session.rollback()
    return reconciled


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invalidate PostgreSQL READY records that have no Qdrant vectors."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist repairs. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()
    records = asyncio.run(reconcile(apply=args.apply))
    mode = "reconciled" if args.apply else "would reconcile"
    print(f"{mode}: {len(records)} document(s)")
    for record in records:
        print(
            f"- {record['course']} / {record['document']} "
            f"(retryable={str(record['retryable']).lower()})"
        )


if __name__ == "__main__":
    main()
