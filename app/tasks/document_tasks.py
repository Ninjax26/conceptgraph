import asyncio
import contextlib
import logging

from celery import Celery
from neo4j import AsyncGraphDatabase

from app.core.config import settings
from app.core.database import AsyncSessionLocal
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


class ProcessingAttemptSuperseded(RuntimeError):
    pass


@celery_app.task(name="process_pdf_task", bind=True)
def process_pdf_task(
    task,
    upload_id: str,
    file_path: str,
    course_uuid: str,
    course_name: str,
    document_name: str,
) -> dict[str, int | str]:
    task_id = str(task.request.id or "")
    parser_service = ParserService()
    upload_service = UploadService()

    async def _run_with_session(coro):
        async with AsyncSessionLocal() as session:
            return await coro(session)

    async def _set_stage(session, stage):
        updated = await upload_service.set_stage(session, upload_id, task_id, stage)
        if not updated:
            raise ProcessingAttemptSuperseded(
                f"Processing attempt {task_id} is no longer current for {upload_id}."
            )

    async def _mark_completed(session, result_json):
        updated = await upload_service.mark_completed(
            session, upload_id, task_id, result_json
        )
        if not updated:
            raise ProcessingAttemptSuperseded(
                f"Processing attempt {task_id} is no longer current for {upload_id}."
            )

    async def _heartbeat_loop(stop: asyncio.Event, superseded: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                current = await _run_with_session(
                    lambda session: upload_service.heartbeat(
                        session, upload_id, task_id
                    )
                )
                if not current:
                    superseded.set()
                    return
            except Exception:
                logger.exception("Heartbeat failed for upload %s", upload_id)
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
            except TimeoutError:
                continue

    async def _ensure_current(superseded: asyncio.Event) -> None:
        if superseded.is_set():
            raise ProcessingAttemptSuperseded(
                f"Processing attempt {task_id} was superseded for {upload_id}."
            )

    async def _run_task() -> dict[str, int | str]:
        task_graph_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        ingestion_service = IngestionService(graph_driver=task_graph_driver)
        heartbeat_stop = asyncio.Event()
        superseded = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(heartbeat_stop, superseded)
        )
        try:
            await _ensure_current(superseded)
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
            vector_count = await asyncio.to_thread(
                ingestion_service.upsert_chunks_to_qdrant, chunks
            )
            await _ensure_current(superseded)
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.EMBEDDED))
            await _run_with_session(lambda session: _set_stage(session, ProcessingStage.BUILDING_GRAPH))
            graph = await ingestion_service.extract_graph_from_chunks(chunks)
            await _ensure_current(superseded)
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
        except ProcessingAttemptSuperseded:
            logger.info("Stopped superseded processing attempt %s for %s", task_id, upload_id)
            return {
                "upload_id": upload_id,
                "course_id": course_uuid,
                "file_path": file_path,
                "status": "superseded",
            }
        except Exception as exc:
            category, retryable, message = classify_failure(exc)
            logger.exception("PDF processing failed for upload %s", upload_id)
            current = await _run_with_session(
                lambda session: upload_service.heartbeat(
                    session, upload_id, task_id
                )
            )
            if current:
                try:
                    await ingestion_service.cleanup_upload(upload_id, course_uuid)
                except Exception:
                    logger.exception("Partial-write cleanup failed for upload %s", upload_id)
                await _run_with_session(
                    lambda session: upload_service.mark_failed(
                        session,
                        upload_id,
                        task_id,
                        message,
                        category,
                        retryable,
                    )
                )
            return {
                "upload_id": upload_id,
                "course_id": course_uuid,
                "file_path": file_path,
                "status": "failed",
                "error": message,
            }
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            await task_graph_driver.close()

    return asyncio.run(_run_task())
