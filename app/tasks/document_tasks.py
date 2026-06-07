import asyncio

from celery import Celery

from app.core.config import settings
from app.services.ingestion_service import IngestionService
from app.services.parser_service import ParserService


celery_app = Celery(
    "conceptgraph",
    broker=settings.redis_url,
    backend=settings.redis_url,
)


@celery_app.task(name="process_pdf_task")
def process_pdf_task(file_path: str, document_id: str) -> dict[str, int | str]:
    parser_service = ParserService()
    ingestion_service = IngestionService()

    chunks = parser_service.parse_and_chunk(file_path=file_path, document_id=document_id)
    result = asyncio.run(ingestion_service.ingest_chunks(chunks))

    return {
        "document_id": document_id,
        "file_path": file_path,
        **result,
    }
