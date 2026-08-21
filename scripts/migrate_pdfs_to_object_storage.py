from __future__ import annotations

import argparse
import asyncio
import hashlib
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, close_database_connections
from app.core.processing import normalize_course_name
from app.models.document_upload import DocumentUpload
from app.services.storage_service import ObjectStorageError, StorageService, storage_service


async def migrate(*, dry_run: bool) -> tuple[int, int]:
    if settings.object_storage_backend != "s3":
        raise RuntimeError("Set OBJECT_STORAGE_BACKEND=s3 before running this migration.")

    migrated = 0
    skipped = 0
    async with AsyncSessionLocal() as session:
        records = list((await session.execute(select(DocumentUpload))).scalars())
        for record in records:
            if not storage_service.is_legacy_reference(record.storage_key):
                skipped += 1
                continue
            try:
                content = await asyncio.to_thread(
                    storage_service.get_bytes,
                    record.storage_key,
                )
                content_hash = record.content_hash or hashlib.sha256(content).hexdigest()
                course_scope = record.course_uuid or str(
                    uuid5(
                        NAMESPACE_URL,
                        f"conceptgraph:course:{normalize_course_name(record.course_id)}",
                    )
                )
                object_key = StorageService.object_key(course_scope, content_hash)
                if not dry_run:
                    await asyncio.to_thread(storage_service.put_pdf, object_key, content)
                    record.storage_key = object_key
                    record.content_hash = content_hash
                migrated += 1
                print(f"{'would migrate' if dry_run else 'migrated'} {record.upload_id}")
            except (OSError, ObjectStorageError) as exc:
                await session.rollback()
                raise RuntimeError(
                    f"Migration stopped at document {record.upload_id}; no local file was deleted."
                ) from exc
        if dry_run:
            await session.rollback()
        else:
            await session.commit()
    return migrated, skipped


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy legacy local PDFs to object storage and update their database keys."
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        migrated, skipped = await migrate(dry_run=args.dry_run)
        print(f"complete: migrated={migrated} skipped={skipped} dry_run={args.dry_run}")
    finally:
        await close_database_connections()


if __name__ == "__main__":
    asyncio.run(main())
