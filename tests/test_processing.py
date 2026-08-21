import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import fitz
from botocore.exceptions import EndpointConnectionError
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.api.endpoints import ingest
from app.core.config import Settings
from app.core.processing import FailureCategory, ProcessingStage, classify_failure, normalize_course_name
from app.services.citation_service import assess_evidence, build_sources
from app.services.course_service import CourseNotReadyError, CourseService
from app.services.exam_service import ExamService
from app.services.rag_service import RetrievalService
from app.services.storage_service import ObjectStorageError, StorageService
from app.services.upload_service import UploadService
from app.schemas.exam import ExamSource
from app.schemas.extraction import ConceptNode, ConceptRelationship, GraphExtractionResponse
from pydantic import ValidationError


class ProcessingRulesTests(unittest.TestCase):
    def test_course_normalization_collapses_case_and_space(self):
        self.assertEqual(normalize_course_name("  CYBER  "), "cyber")
        self.assertEqual(normalize_course_name("Cyber"), "cyber")
        self.assertEqual(normalize_course_name("cyber"), "cyber")

    def test_configuration_failure_is_permanent(self):
        category, retryable, _ = classify_failure(RuntimeError("GROQ_API_KEY is not configured"))
        self.assertEqual(category, FailureCategory.CONFIGURATION_ERROR)
        self.assertFalse(retryable)

    def test_worker_failure_is_retryable(self):
        category, retryable, _ = classify_failure(RuntimeError("worker interrupted"))
        self.assertEqual(category, FailureCategory.WORKER_ERROR)
        self.assertTrue(retryable)

    def test_citations_deduplicate_and_hide_internal_ids(self):
        chunk = {
            "id": "internal-vector-uuid",
            "text": "Supporting passage",
            "score": 0.8,
            "metadata": {
                "upload_id": "document-uuid",
                "document_name": "Cyber.pdf",
                "page_number": 6,
                "section_heading": "Cyber Hygiene",
            },
        }
        sources = build_sources([chunk, chunk])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_id"], "source-1")
        self.assertNotIn("internal-vector-uuid", str(sources))
        self.assertEqual(sources[0]["document_name"], "Cyber.pdf")

    def test_graph_rejects_relationships_with_missing_entities(self):
        with self.assertRaises(ValidationError):
            GraphExtractionResponse(
                nodes=[ConceptNode(id="known", name="Known", type="concept")],
                relationships=[
                    ConceptRelationship(
                        source_node_id="missing",
                        target_node_id="known",
                        relation_type="PREREQUISITE_OF",
                    )
                ],
            )

    def test_graph_deduplicates_identical_relationships(self):
        relationship = ConceptRelationship(
            source_node_id="a",
            target_node_id="b",
            relation_type="RELATED_TO",
        )
        graph = GraphExtractionResponse(
            nodes=[
                ConceptNode(id="a", name="A", type="concept"),
                ConceptNode(id="b", name="B", type="concept"),
            ],
            relationships=[relationship, relationship],
        )
        self.assertEqual(len(graph.relationships), 1)

    def test_neo4j_relationship_uses_mapping_interface(self):
        class FakeRelationship:
            type = "PREREQUISITE_OF"
            start_node = {"id": "source"}
            end_node = {"id": "target"}

            def items(self):
                return [("document_name", "Course.pdf")]

            def __iter__(self):
                return iter(["not-a-key-value-pair"])

        serialized = RetrievalService._relationship_to_dict(FakeRelationship())

        self.assertEqual(serialized["type"], "PREREQUISITE_OF")
        self.assertEqual(serialized["source"], "source")
        self.assertEqual(serialized["target"], "target")
        self.assertEqual(serialized["document_name"], "Course.pdf")

    def test_bidirectional_graph_query_preserves_native_edge_direction(self):
        generated = RetrievalService._fallback_cypher("zero trust")

        self.assertIn("(concept)-[relationship]-(related:Concept)", generated.cypher)
        self.assertIn("related_concepts", generated.cypher)

    def test_evidence_threshold_removes_irrelevant_passages(self):
        relevant = {
            "text": "Strongly relevant passage",
            "score": 0.82,
            "rerank_score": 5.0,
            "metadata": {},
        }
        irrelevant = {
            "text": "Unrelated passage",
            "score": 0.05,
            "rerank_score": -10.0,
            "metadata": {},
        }

        chunks, confidence = assess_evidence([irrelevant, relevant])

        self.assertEqual([chunk["text"] for chunk in chunks], [relevant["text"]])
        self.assertEqual(confidence["evidence_count"], 1)
        self.assertIn(confidence["level"], {"medium", "low"})

    def test_exam_sources_are_balanced_and_citations_are_enriched(self):
        chunks = [
            {
                "text": "Alpha evidence",
                "metadata": {
                    "document_name": "A.pdf",
                    "page_number": 1,
                    "section_heading": "Alpha",
                    "chunk_index": 0,
                },
            },
            {
                "text": "Beta evidence",
                "metadata": {
                    "document_name": "B.pdf",
                    "page_number": 4,
                    "section_heading": "Beta",
                    "chunk_index": 0,
                },
            },
        ]
        sources = ExamService._select_exam_sources(chunks)
        raw = """{
          "questions": [{
            "question_text": "Which statement is supported?",
            "options": ["Alpha", "One", "Two", "Three"],
            "correct_answer": "Alpha",
            "explanation": "Alpha is supported by the cited passage.",
            "topic": "Alpha",
            "citation_ids": ["exam-source-1"]
          }]
        }"""

        questions = ExamService._parse_questions(raw, sources)

        self.assertEqual({source.document_name for source in sources}, {"A.pdf", "B.pdf"})
        self.assertEqual(questions[0].sources[0].source_id, "exam-source-1")
        self.assertTrue(questions[0].sources[0].supporting_passage)

    def test_exam_question_without_valid_citation_is_rejected(self):
        source = ExamSource(
            source_id="exam-source-1",
            document_name="Course.pdf",
            page_number=1,
            supporting_passage="Evidence",
        )
        raw = """{
          "questions": [{
            "question_text": "Unsupported?",
            "options": ["A", "B", "C", "D"],
            "correct_answer": "A",
            "explanation": "No valid citation.",
            "topic": "Test",
            "citation_ids": ["invented-source"]
          }]
        }"""

        self.assertEqual(ExamService._parse_questions(raw, [source]), [])

    def test_render_postgres_url_uses_asyncpg_driver(self):
        config = Settings(DATABASE_URL="postgresql://user:password@db/course")

        self.assertEqual(
            config.postgres_dsn,
            "postgresql+asyncpg://user:password@db/course",
        )

    def test_pdf_preview_supports_byte_ranges(self):
        response = ingest._pdf_response(b"0123456789", "Course Notes.pdf", "bytes=2-5")

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.body, b"2345")
        self.assertEqual(response.headers["content-range"], "bytes 2-5/10")


class StorageServiceTests(unittest.TestCase):
    class FakeBody:
        def __init__(self, content: bytes):
            self.content = content
            self.closed = False

        def read(self):
            return self.content

        def close(self):
            self.closed = True

    class FakeS3:
        def __init__(self):
            self.objects: dict[tuple[str, str], bytes] = {}

        def head_bucket(self, **kwargs):
            return {}

        def put_object(self, **kwargs):
            self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

        def get_object(self, **kwargs):
            return {
                "Body": StorageServiceTests.FakeBody(
                    self.objects[(kwargs["Bucket"], kwargs["Key"])]
                )
            }

        def head_object(self, **kwargs):
            self.objects[(kwargs["Bucket"], kwargs["Key"])]
            return {}

        def delete_object(self, **kwargs):
            self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)

    def test_s3_round_trip_uses_content_addressed_key(self):
        client = self.FakeS3()
        config = Settings(
            OBJECT_STORAGE_BACKEND="s3",
            S3_BUCKET="test-pdfs",
            S3_ENDPOINT_URL="https://objects.example.test",
            S3_AUTO_CREATE_BUCKET=False,
        )
        service = StorageService(config, client=client)
        key = service.object_key("course-1", "abc123")

        service.put_pdf(key, b"pdf-content")

        self.assertEqual(key, "courses/course-1/documents/abc123.pdf")
        self.assertTrue(service.exists(key))
        self.assertEqual(service.get_bytes(key), b"pdf-content")
        service.delete(key)
        self.assertFalse(client.objects)

    def test_legacy_local_reference_remains_readable_for_migration(self):
        with tempfile.TemporaryDirectory() as upload_dir:
            path = Path(upload_dir) / "legacy.pdf"
            path.write_bytes(b"legacy-pdf")
            config = Settings(
                OBJECT_STORAGE_BACKEND="local",
                LEGACY_UPLOAD_DIR=Path(upload_dir),
            )
            service = StorageService(config)

            self.assertTrue(service.is_legacy_reference(str(path)))
            self.assertEqual(service.get_bytes(str(path)), b"legacy-pdf")

    def test_network_failure_is_normalized_to_storage_error(self):
        client = self.FakeS3()
        client.head_bucket = lambda **kwargs: (_ for _ in ()).throw(
            EndpointConnectionError(endpoint_url="https://objects.example.test")
        )
        config = Settings(
            OBJECT_STORAGE_BACKEND="s3",
            S3_BUCKET="test-pdfs",
            S3_ENDPOINT_URL="https://objects.example.test",
        )

        with self.assertRaises(ObjectStorageError):
            StorageService(config, client=client).put_pdf("document.pdf", b"pdf")


class UploadEndpointTests(unittest.TestCase):
    def test_upload_writes_content_addressed_object_before_enqueuing(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Object storage test")
        content = document.tobytes()
        document.close()
        upload = UploadFile(
            BytesIO(content),
            size=len(content),
            filename="course.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        )
        course = SimpleNamespace(id="course-uuid", display_name="CYBER")
        db = SimpleNamespace(execute=AsyncMock(), rollback=AsyncMock())

        with (
            patch.object(
                ingest.course_service,
                "get_or_create",
                AsyncMock(return_value=course),
            ),
            patch.object(
                ingest.upload_service,
                "find_duplicate",
                AsyncMock(return_value=None),
            ),
            patch.object(ingest.upload_service, "create_upload", AsyncMock()),
            patch.object(ingest.storage_service, "put_pdf") as put_pdf,
            patch.object(
                ingest.process_pdf_task,
                "apply_async",
                return_value=SimpleNamespace(id="task-1"),
            ) as enqueue,
        ):
            response = asyncio.run(ingest.upload_document(" CYBER ", upload, db))

        key = put_pdf.call_args.args[0]
        self.assertTrue(key.startswith("courses/course-uuid/documents/"))
        self.assertTrue(key.endswith(".pdf"))
        self.assertEqual(put_pdf.call_args.args[1], content)
        self.assertEqual(response.task_id, "task-1")
        self.assertEqual(enqueue.call_args.kwargs["args"][1], key)

    def test_failed_duplicate_deletion_keeps_shared_object(self):
        record = SimpleNamespace(
            upload_id="failed-duplicate",
            storage_key="courses/course-1/documents/hash.pdf",
        )
        db = SimpleNamespace(rollback=AsyncMock())

        with (
            patch.object(
                ingest.upload_service,
                "lock_failed_for_deletion",
                AsyncMock(return_value=record),
            ),
            patch.object(
                ingest.upload_service,
                "storage_key_is_shared",
                AsyncMock(return_value=True),
            ),
            patch.object(
                ingest.upload_service,
                "delete_failed",
                AsyncMock(return_value=record),
            ),
            patch.object(ingest.storage_service, "delete") as delete_object,
        ):
            asyncio.run(ingest.remove_failed_upload(record.upload_id, db))

        delete_object.assert_not_called()


class ProcessingFencingTests(unittest.TestCase):
    def test_stale_or_failed_attempt_cannot_advance_stage(self):
        service = UploadService()
        service._get_current_upload = AsyncMock(
            return_value=SimpleNamespace(status="failed")
        )
        session = SimpleNamespace(commit=AsyncMock())

        updated = asyncio.run(
            service.set_stage(
                session,
                "upload-1",
                "stale-task",
                ProcessingStage.EMBEDDING,
            )
        )

        self.assertFalse(updated)
        session.commit.assert_not_awaited()


class ReadyContextTests(unittest.TestCase):
    def test_course_without_ready_documents_is_rejected(self):
        service = CourseService()
        course = SimpleNamespace(id="course-uuid", display_name="CYBER")
        service.resolve = AsyncMock(return_value=course)
        result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        session = SimpleNamespace(execute=AsyncMock(return_value=result))

        with self.assertRaises(CourseNotReadyError):
            asyncio.run(service.get_ready_context(session, "cyber"))

    def test_course_summary_excludes_historical_duplicate_hashes(self):
        service = CourseService()
        now = datetime.now(timezone.utc)
        course = SimpleNamespace(id="course-uuid", display_name="CYBER")
        documents = [
            SimpleNamespace(
                upload_id="new",
                course_uuid=course.id,
                content_hash="same-hash",
                status="ready",
                updated_at=now,
                processed_chunk_count=7,
                graph_node_count=18,
                graph_edge_count=14,
            ),
            SimpleNamespace(
                upload_id="old",
                course_uuid=course.id,
                content_hash="same-hash",
                status="ready",
                updated_at=now,
                processed_chunk_count=7,
                graph_node_count=18,
                graph_edge_count=14,
            ),
        ]
        courses_result = SimpleNamespace(scalars=lambda: [course])
        documents_result = SimpleNamespace(scalars=lambda: documents)
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[courses_result, documents_result])
        )

        summaries = asyncio.run(service.list_summaries(session))

        self.assertEqual(summaries[0].total_documents, 1)
        self.assertEqual(summaries[0].ready_documents, 1)
        self.assertEqual(summaries[0].processed_chunk_count, 7)
        self.assertEqual(summaries[0].duplicate_records, 1)


class RetryEndpointTests(unittest.TestCase):
    def test_worker_enqueue_failure_returns_document_to_failed_state(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            record = SimpleNamespace(
                upload_id="upload-1",
                task_id="old-task",
                course_uuid="course-uuid",
                course_id="CYBER",
                original_filename="course.pdf",
                storage_key=str(Path(source.name)),
                stage="FAILED",
                retryable=True,
            )
            retried_record = SimpleNamespace(**vars(record))
            retried_record.task_id = "new-task"
            retried_record.stage = "UPLOADED"
            retried_record.retryable = False

            with (
                patch.object(ingest.upload_service, "get_upload", AsyncMock(return_value=record)),
                patch.object(
                    ingest.upload_service,
                    "retry_upload",
                    AsyncMock(return_value=retried_record),
                ),
                patch.object(ingest.upload_service, "mark_failed", AsyncMock()) as mark_failed,
                patch.object(
                    ingest.process_pdf_task,
                    "apply_async",
                    side_effect=ConnectionError("broker unavailable"),
                ),
                patch.object(ingest.storage_service, "exists", return_value=True),
            ):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(ingest.retry_upload(record.upload_id, SimpleNamespace()))

            self.assertEqual(raised.exception.status_code, 503)
            mark_failed.assert_awaited_once_with(
                ANY,
                record.upload_id,
                ANY,
                "The processing worker is unavailable. Please retry when the service is restored.",
                FailureCategory.WORKER_ERROR,
                True,
            )


if __name__ == "__main__":
    unittest.main()
