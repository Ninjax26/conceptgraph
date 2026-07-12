import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.processing import FailureCategory, classify_failure, normalize_course_name
from app.services.citation_service import build_sources
from app.services.course_service import CourseNotReadyError, CourseService
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


if __name__ == "__main__":
    unittest.main()
