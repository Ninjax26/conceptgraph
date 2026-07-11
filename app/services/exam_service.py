"""Syllabus-Bounded Exam Generator service.

Retrieves text chunks from Qdrant using strict course filtering and instructs
the LLM to generate a multiple-choice exam
purely from the retrieved syllabus content.
"""

import asyncio
import json
import logging
from typing import Any

from groq import Groq
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import FieldCondition, Filter, MatchAny

from app.core.config import settings
from app.core.database import qdrant_client as default_qdrant_client
from app.core.exceptions import LLMConfigurationError
from app.schemas.exam import ExamResponse, MockQuestion
from app.services.course_service import ReadyCourseContext

logger = logging.getLogger(__name__)


class ExamService:
    """Generates syllabus-bounded mock exams from Qdrant + LLM."""

    def __init__(
        self,
        vector_client: QdrantClient = default_qdrant_client,
    ) -> None:
        self.vector_client = vector_client
        self.collection_name = settings.qdrant_collection_name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_exam(
        self,
        context: ReadyCourseContext,
        num_questions: int = 5,
    ) -> ExamResponse:
        """End-to-end exam generation pipeline.

        1. Metadata-filter Qdrant for chunks matching the course.
        2. Concatenate the chunk texts into a bounded context window.
        3. Ask the LLM to produce *num_questions* MCQs strictly from that
           context, returning structured JSON conforming to ExamResponse.
        """
        # Step 1 – metadata-filtered retrieval
        chunks = await asyncio.to_thread(
            self._retrieve_chunks_by_metadata,
            context.document_ids,
        )

        if not chunks:
            logger.warning(
                "No chunks found for course_id=%s; returning empty exam.",
                context.course.display_name,
            )
            return ExamResponse(
                course_id=context.course.id,
                questions=[],
                source_count=0,
            )

        # Step 2 – constrained generation
        context_text = self._build_context(chunks)
        questions = await self._generate_questions(
            context_text=context_text,
            num_questions=num_questions,
        )
        if len(questions) != num_questions:
            raise RuntimeError(
                f"Exam generation produced {len(questions)} valid questions; expected {num_questions}."
            )

        return ExamResponse(
            course_id=context.course.id,
            questions=questions,
            source_count=min(6, len(chunks)),
        )

    # ------------------------------------------------------------------
    # Step 1: Metadata-filtered retrieval (no semantic search)
    # ------------------------------------------------------------------

    def _retrieve_chunks_by_metadata(
        self,
        document_ids: list[str],
        batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Scroll through Qdrant with a strict metadata filter.

        Returns all matching payloads without a query vector – this is a
        pure filter-based retrieval.
        """
        if not self._collection_exists():
            logger.info("Qdrant collection %s does not exist yet.", self.collection_name)
            return []

        query_filter = Filter(
            must=[
                FieldCondition(
                    key="upload_id",
                    match=MatchAny(any=document_ids),
                ),
            ]
        )

        all_chunks: list[dict[str, Any]] = []
        offset = None

        while True:
            try:
                # qdrant-client >=1.7 exposes `scroll`
                scroll_kwargs: dict[str, Any] = {
                    "collection_name": self.collection_name,
                    "scroll_filter": query_filter,
                    "limit": batch_size,
                    "with_payload": True,
                    "with_vectors": False,
                }
                if offset is not None:
                    scroll_kwargs["offset"] = offset

                points, next_offset = self.vector_client.scroll(**scroll_kwargs)
            except UnexpectedResponse as exc:
                if self._is_qdrant_not_found_error(exc):
                    logger.info("Qdrant collection %s does not exist yet.", self.collection_name)
                    return []
                raise
            except TypeError:
                # Older qdrant-client versions use positional / different kwarg names.
                try:
                    points, next_offset = self.vector_client.scroll(
                        collection_name=self.collection_name,
                        scroll_filter=query_filter,
                        limit=batch_size,
                        with_payload=True,
                    )
                except UnexpectedResponse as exc:
                    if self._is_qdrant_not_found_error(exc):
                        logger.info("Qdrant collection %s does not exist yet.", self.collection_name)
                        return []
                    raise

            for point in points:
                payload = point.payload or {}
                all_chunks.append(
                    {
                        "id": str(point.id),
                        "text": str(payload.get("text", "")),
                        "metadata": {
                            key: value
                            for key, value in payload.items()
                            if key != "text"
                        },
                    }
                )

            if next_offset is None:
                break
            offset = next_offset

        logger.info(
            "Retrieved %d chunks for %d ready documents",
            len(all_chunks),
            len(document_ids),
        )
        return all_chunks

    def _collection_exists(self) -> bool:
        try:
            return bool(
                self.vector_client.collection_exists(
                    collection_name=self.collection_name,
                )
            )
        except UnexpectedResponse as exc:
            if self._is_qdrant_not_found_error(exc):
                return False
            raise
        except AttributeError:
            try:
                self.vector_client.get_collection(collection_name=self.collection_name)
            except Exception as exc:
                if "not found" in str(exc).lower() or "404" in str(exc):
                    return False
                raise
            return True

    @staticmethod
    def _is_qdrant_not_found_error(exc: UnexpectedResponse) -> bool:
        return exc.status_code == 404 and b"Collection" in exc.content

    # ------------------------------------------------------------------
    # Step 2: Constrained LLM generation
    # ------------------------------------------------------------------

    async def _generate_questions(
        self,
        context_text: str,
        num_questions: int,
    ) -> list[MockQuestion]:
        """Dispatch to the configured LLM provider."""
        provider = settings.llm_provider.lower()
        if provider == "gemini":
            return await asyncio.to_thread(
                self._generate_with_gemini, context_text, num_questions,
            )
        if provider == "groq":
            return await asyncio.to_thread(
                self._generate_with_groq, context_text, num_questions,
            )
        raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")

    def _generate_with_groq(
        self,
        context_text: str,
        num_questions: int,
    ) -> list[MockQuestion]:
        if not settings.groq_api_key:
            raise LLMConfigurationError("GROQ_API_KEY is required when LLM_PROVIDER=groq")

        client = Groq(api_key=settings.groq_api_key)
        completion = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": self._system_prompt(num_questions)},
                {"role": "user", "content": self._user_prompt(context_text)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )

        raw = completion.choices[0].message.content or "{}"
        return self._parse_questions(raw)

    def _generate_with_gemini(
        self,
        context_text: str,
        num_questions: int,
    ) -> list[MockQuestion]:
        if not settings.gemini_api_key:
            raise LLMConfigurationError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")

        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(
            [
                self._system_prompt(num_questions),
                self._user_prompt(context_text),
            ],
            generation_config={
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        )
        return self._parse_questions(response.text or "{}")

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _system_prompt(num_questions: int) -> str:
        schema = json.dumps(ExamResponse.model_json_schema(), indent=2)
        return (
            "You are ConceptGraph Exam Generator – a syllabus-bounded academic "
            "assessment engine. Your ONLY job is to produce a strict JSON exam.\n\n"
            "RULES:\n"
            f"1. Generate exactly {num_questions} multiple-choice questions.\n"
            "2. Every question must be STRICTLY derived from the provided text context. "
            "Do NOT use external knowledge.\n"
            "3. Each question must have exactly 4 options.\n"
            "4. The 'correct_answer' field must be one of the 4 options verbatim.\n"
            "5. The 'explanation' must cite specific information from the provided "
            "context that justifies the correct answer.\n"
            "6. Output ONLY a JSON object with a single key 'questions' containing "
            "the list of question objects.\n\n"
            f"Response JSON schema:\n{schema}"
        )

    @staticmethod
    def _user_prompt(context_text: str) -> str:
        return (
            "Generate the exam questions based EXCLUSIVELY on the following "
            "syllabus content. Do not add any information not present in this text.\n\n"
            "--- BEGIN SYLLABUS CONTENT ---\n"
            f"{context_text}\n"
            "--- END SYLLABUS CONTENT ---"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_context(chunks: list[dict[str, Any]]) -> str:
        """Build a representative context that stays within provider limits."""
        if not chunks:
            return ""
        sample_count = min(6, len(chunks))
        sample_indexes = {
            round(index * (len(chunks) - 1) / max(sample_count - 1, 1))
            for index in range(sample_count)
        }
        return "\n\n".join(
            f"[Course excerpt {index + 1}]\n{str(chunks[index].get('text', ''))[:600]}"
            for index in sorted(sample_indexes)
            if chunks[index].get("text")
        )

    @staticmethod
    def _parse_questions(raw_json: str) -> list[MockQuestion]:
        """Parse LLM JSON output into validated MockQuestion objects."""
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", exc)
            raise RuntimeError("LLM returned invalid exam JSON.") from exc

        # The LLM may return {"questions": [...]} or just [...]
        question_dicts: list[dict[str, Any]]
        if isinstance(data, list):
            question_dicts = data
        elif isinstance(data, dict):
            question_dicts = data.get("questions", [])
        else:
            logger.error("Unexpected LLM response structure: %s", type(data))
            raise RuntimeError("LLM returned an unexpected exam JSON structure.")

        questions: list[MockQuestion] = []
        for item in question_dicts:
            try:
                questions.append(MockQuestion.model_validate(item))
            except Exception as exc:
                logger.warning("Skipping malformed question: %s", exc)
                continue

        return questions
