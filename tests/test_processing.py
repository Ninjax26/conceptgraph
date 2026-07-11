import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.processing import FailureCategory, classify_failure, normalize_course_name
from app.services.citation_service import build_sources
from app.services.course_service import CourseNotReadyError, CourseService


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


class ReadyContextTests(unittest.TestCase):
    def test_course_without_ready_documents_is_rejected(self):
        service = CourseService()
        course = SimpleNamespace(id="course-uuid", display_name="CYBER")
        service.resolve = AsyncMock(return_value=course)
        result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        session = SimpleNamespace(execute=AsyncMock(return_value=result))

        with self.assertRaises(CourseNotReadyError):
            asyncio.run(service.get_ready_context(session, "cyber"))


if __name__ == "__main__":
    unittest.main()
