import asyncio

from celery import Celery

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import LLMConfigurationError
from app.services.upload_service import UploadService
from app.services.ingestion_service import IngestionService
from app.services.parser_service import ParserService


celery_app = Celery(
    "conceptgraph",
    broker=settings.redis_url,
    backend=settings.redis_url,
)


@celery_app.task(name="process_pdf_task")
def process_pdf_task(
    upload_id: str,
    file_path: str,
    document_id: str,
    week_number: int = 1,
) -> dict[str, int | str]:
    parser_service = ParserService()
    ingestion_service = IngestionService()
    upload_service = UploadService()

    async def _run_with_session(coro):
        async with AsyncSessionLocal() as session:
            return await coro(session)

    async def _mark_running(session):
        await upload_service.mark_running(session, upload_id)

    async def _mark_completed(session, result_json):
        await upload_service.mark_completed(session, upload_id, result_json)

    async def _mark_failed(session, error_message):
        await upload_service.mark_failed(session, upload_id, error_message)

    try:
        asyncio.run(_run_with_session(_mark_running))
        chunks = parser_service.parse_and_chunk(
            file_path=file_path,
            document_id=document_id,
            upload_id=upload_id,
            week_number=week_number,
        )
        result = asyncio.run(ingestion_service.ingest_chunks(chunks))
        asyncio.run(_run_with_session(lambda session: _mark_completed(session, result)))
    except LLMConfigurationError as exc:
        asyncio.run(_run_with_session(lambda session: _mark_failed(session, str(exc))))
        return {
            "upload_id": upload_id,
            "document_id": document_id,
            "week_number": week_number,
            "file_path": file_path,
            "status": "failed",
            "error": f"LLM provider is not configured: {exc}",
        }
    except Exception as exc:
        asyncio.run(_run_with_session(lambda session: _mark_failed(session, str(exc))))
        return {
            "upload_id": upload_id,
            "document_id": document_id,
            "week_number": week_number,
            "file_path": file_path,
            "status": "failed",
            "error": str(exc),
        }

    return {
        "upload_id": upload_id,
        "document_id": document_id,
        "week_number": week_number,
        "file_path": file_path,
        "status": "completed",
        **result,
    }
