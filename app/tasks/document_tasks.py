import asyncio
import logging

from celery import Celery
from neo4j import AsyncGraphDatabase

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import LLMConfigurationError
from app.core.processing import ProcessingStage, classify_failure
from app.services.upload_service import UploadService
from app.services.ingestion_service import IngestionService
from app.services.parser_service import ParserService

logger = logging.getLogger(__name__)


celery_app = Celery(
    "conceptgraph",
    broker=settings.redis_url,
    backend=settings.redis_url,
)


@celery_app.task(name="process_pdf_task")
def process_pdf_task(
    upload_id: str,
    file_path: str,
    course_uuid: str,
    course_name: str,
    document_name: str,
) -> dict[str, int | str]:
    parser_service = ParserService()
    upload_service = UploadService()

    async def _run_with_session(coro):
        async with AsyncSessionLocal() as session:
            return await coro(session)

    async def _set_stage(session, stage):
        await upload_service.set_stage(session, upload_id, stage)

    async def _mark_completed(session, result_json):
        await upload_service.mark_completed(session, upload_id, result_json)

    async def _run_task() -> dict[str, int | str]:
        task_graph_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        ingestion_service = IngestionService(graph_driver=task_graph_driver)
        try:
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.EXTRACTING))
            pages = parser_service.extract_pages(file_path)
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.EXTRACTED))
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.CHUNKING))
            chunks = parser_service.chunk_pages(
                pages, file_path, course_uuid, upload_id, document_name
            )
            if not chunks:
                raise ValueError(
                    "No extractable text was found in this PDF. Scanned PDFs need OCR before ingestion."
                )
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.CHUNKED))
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.EMBEDDING))
            vector_count = ingestion_service.upsert_chunks_to_qdrant(chunks)
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.EMBEDDED))
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.BUILDING_GRAPH))
            graph = await ingestion_service.extract_graph_from_chunks(chunks)
            await ingestion_service.store_graph_extraction(
                graph, course_uuid, upload_id=upload_id, document_name=document_name,
                course_name=course_name,
            )
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.GRAPH_BUILT))
            result = {
                "chunks_indexed": vector_count,
                "nodes_upserted": len(graph.nodes),
                "relationships_upserted": len(graph.relationships),
            }
            await _run_with_session(lambda session: _mark_completed(session, result))

            return {
                "upload_id": upload_id,
                "course_id": course_uuid,
                "file_path": file_path,
                "status": "ready",
                **result,
            }
        except LLMConfigurationError as exc:
            category, retryable, message = classify_failure(exc)
            logger.exception("PDF processing configuration failed for upload %s", upload_id)
            try:
                await ingestion_service.cleanup_upload(upload_id, course_uuid)
            except Exception:
                logger.exception("Partial-write cleanup failed for upload %s", upload_id)
            await _run_with_session(lambda session: upload_service.mark_failed(session, upload_id, message, category, retryable))
            return {
                "upload_id": upload_id,
                "course_id": course_uuid,
                "file_path": file_path,
                "status": "failed",
                "error": message,
            }
        except Exception as exc:
            category, retryable, message = classify_failure(exc)
            logger.exception("PDF processing failed for upload %s", upload_id)
            try:
                await ingestion_service.cleanup_upload(upload_id, course_uuid)
            except Exception:
                logger.exception("Partial-write cleanup failed for upload %s", upload_id)
            await _run_with_session(lambda session: upload_service.mark_failed(session, upload_id, message, category, retryable))
            return {
                "upload_id": upload_id,
                "course_id": course_uuid,
                "file_path": file_path,
                "status": "failed",
                "error": message,
            }
        finally:
            await task_graph_driver.close()

    return asyncio.run(_run_task())
