import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import torch
from groq import Groq
from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.core.database import neo4j_driver, qdrant_client

logger = logging.getLogger(__name__)


class CypherGenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cypher: str = Field(..., min_length=1)
    parameters: dict[str, str | int | float | bool | list[str]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphRetrievalResult:
    concepts: list[dict[str, Any]]
    prerequisite_names: list[str]
    cypher: str


class RetrievalService:
    def __init__(
        self,
        graph_driver: AsyncDriver = neo4j_driver,
        vector_client: QdrantClient = qdrant_client,
    ) -> None:
        self.graph_driver = graph_driver
        self.vector_client = vector_client
        self.collection_name = settings.qdrant_collection_name
        self._embedding_model: SentenceTransformer | None = None

    async def retrieve(
        self,
        question: str,
        course_id: str,
        week_number: int,
        top_k: int = 10,
    ) -> dict[str, Any]:
        graph_result = await self.execute_graph_retrieval(
            question=question,
            course_id=course_id,
            week_number=week_number,
        )
        chunks = await asyncio.to_thread(
            self.search_qdrant,
            question,
            graph_result.prerequisite_names,
            course_id,
            week_number,
            top_k,
        )
        return {
            "graph_context": graph_result.concepts,
            "graph_cypher": graph_result.cypher,
            "chunks": chunks,
        }

    async def execute_graph_retrieval(
        self,
        question: str,
        course_id: str,
        week_number: int,
    ) -> GraphRetrievalResult:
        try:
            generated = await self.generate_cypher(question, week_number)
            cypher = self._validate_read_only_cypher(generated.cypher)
            if "$course_id" not in cypher or "$week_number" not in cypher:
                generated = self._fallback_cypher(question, week_number)
                cypher = self._validate_read_only_cypher(generated.cypher)
        except Exception as exc:
            logger.warning("Falling back to local course-scoped Cypher: %s", exc)
            generated = self._fallback_cypher(question, week_number)
            cypher = self._validate_read_only_cypher(generated.cypher)
        parameters = {
            **generated.parameters,
            "question": question,
            "course_id": course_id,
            "week_number": week_number,
        }

        async with self.graph_driver.session() as session:
            result = await session.run(cypher, parameters)
            records = await result.data()

            if not records:
                records = await self._fetch_course_graph(session, course_id, week_number)

        concepts: list[dict[str, Any]] = []
        prerequisite_names: list[str] = []
        for record in records:
            concept = self._node_to_dict(record.get("concept"))
            prerequisites = [
                self._node_to_dict(node)
                for node in record.get("prerequisites", [])
                if node is not None
            ]
            concepts.append({"concept": concept, "prerequisites": prerequisites})
            prerequisite_names.extend(
                str(node["name"])
                for node in prerequisites
                if node.get("name")
            )

        return GraphRetrievalResult(
            concepts=concepts,
            prerequisite_names=sorted(set(prerequisite_names)),
            cypher=cypher,
        )

    async def generate_cypher(self, question: str, week_number: int) -> CypherGenerationResponse:
        provider = settings.llm_provider.lower()
        if provider == "gemini":
            return await asyncio.to_thread(
                self._generate_cypher_with_gemini,
                question,
                week_number,
            )
        if provider == "groq":
            return await asyncio.to_thread(
                self._generate_cypher_with_groq,
                question,
                week_number,
            )
        raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")

    def search_qdrant(
        self,
        question: str,
        prerequisite_names: list[str],
        course_id: str,
        week_number: int,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        if not self._collection_exists():
            logger.info("Qdrant collection %s does not exist yet.", self.collection_name)
            return []

        expanded_query = self._build_expanded_query(question, prerequisite_names)
        query_vector = self.embedding_model.encode(
            expanded_query,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="document_id",
                    match=MatchValue(value=course_id),
                ),
                FieldCondition(
                    key="week",
                    match=MatchValue(value=week_number),
                )
            ]
        )

        try:
            results = self.vector_client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            ).points
        except AttributeError:
            results = self.vector_client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            )

        chunks: list[dict[str, Any]] = []
        for point in results:
            payload = point.payload or {}
            chunks.append(
                {
                    "id": str(point.id),
                    "score": float(point.score),
                    "text": str(payload.get("text", "")),
                    "metadata": {
                        key: value
                        for key, value in payload.items()
                        if key != "text"
                    },
                }
            )
        return chunks

    @property
    def embedding_model(self) -> SentenceTransformer:
        if self._embedding_model is None:
            self._embedding_model = SentenceTransformer(
                settings.embedding_model_name,
                device=self._resolve_embedding_device(),
            )
        return self._embedding_model

    def _collection_exists(self) -> bool:
        try:
            return bool(
                self.vector_client.collection_exists(
                    collection_name=self.collection_name,
                )
            )
        except AttributeError:
            try:
                self.vector_client.get_collection(collection_name=self.collection_name)
            except Exception as exc:
                if "not found" in str(exc).lower() or "404" in str(exc):
                    return False
                raise
            return True

    def _generate_cypher_with_groq(
        self,
        question: str,
        week_number: int,
    ) -> CypherGenerationResponse:
        if not settings.groq_api_key:
            return self._fallback_cypher(question, week_number)

        client = Groq(api_key=settings.groq_api_key)
        completion = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": self._cypher_system_prompt()},
                {"role": "user", "content": question},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content or "{}"
        return CypherGenerationResponse.model_validate_json(content)

    def _generate_cypher_with_gemini(
        self,
        question: str,
        week_number: int,
    ) -> CypherGenerationResponse:
        if not settings.gemini_api_key:
            return self._fallback_cypher(question, week_number)

        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(
            [self._cypher_system_prompt(), question],
            generation_config={
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        )
        return CypherGenerationResponse.model_validate_json(response.text or "{}")

    @staticmethod
    def _cypher_system_prompt() -> str:
        schema = json.dumps(CypherGenerationResponse.model_json_schema(), indent=2)
        return (
            "Generate a single read-only Neo4j Cypher query for an academic concept graph. "
            "The graph uses (:Course {id}) nodes connected to "
            "(:Concept {id, name, type, description, week}) nodes by [:CONTAINS]. "
            "Always scope the query to MATCH (course:Course {id: $course_id}) and only "
            "return concepts contained by that course and the requested week_number. "
            "Always include `week_number` in any concept or prerequisite filters. "
            "The query must return a variable named concept and a variable named prerequisites. "
            "prerequisites must contain prerequisite Concept nodes up to 2 hops away. "
            "Use parameters instead of interpolating user text. Do not write, merge, delete, "
            "create, set, call procedures, or use APOC. Return only JSON matching this schema:\n\n"
            f"{schema}"
        )

    @staticmethod
    def _fallback_cypher(question: str, week_number: int) -> CypherGenerationResponse:
        terms = [
            term.lower()
            for term in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", question)
            if len(term) > 2
        ][:12]
        return CypherGenerationResponse(
            cypher="""
            MATCH (course:Course {id: $course_id})-[:CONTAINS]->(concept:Concept)
            WHERE concept.week = $week_number
              AND any(term IN $terms WHERE toLower(concept.name) CONTAINS term)
            OPTIONAL MATCH (prerequisite:Concept)-[*1..2]->(concept)
            WHERE prerequisite IS NULL OR ((course)-[:CONTAINS]->(prerequisite) AND prerequisite.week = $week_number)
            RETURN concept, collect(DISTINCT prerequisite) AS prerequisites
            LIMIT 5
            """,
            parameters={"terms": terms or [question.lower()]},
        )

    @staticmethod
    def _validate_read_only_cypher(cypher: str) -> str:
        stripped = cypher.strip()
        forbidden = re.compile(
            r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL|LOAD|FOREACH)\b",
            re.IGNORECASE,
        )
        if forbidden.search(stripped):
            raise ValueError("Generated Cypher contains a forbidden write operation.")
        if "concept" not in stripped or "prerequisites" not in stripped:
            raise ValueError("Generated Cypher must return concept and prerequisites.")
        return stripped

    @staticmethod
    def _node_to_dict(node: Any) -> dict[str, Any]:
        if node is None:
            return {}
        return dict(node)

    @staticmethod
    def _build_expanded_query(question: str, prerequisite_names: list[str]) -> str:
        if not prerequisite_names:
            return question
        graph_terms = " ".join(prerequisite_names)
        return f"{question}\nRelevant prerequisite concepts: {graph_terms}"

    async def _fetch_course_graph(
        self,
        session,
        course_id: str,
        week_number: int,
    ) -> list[dict[str, Any]]:
        result = await session.run(
            """
            MATCH (course:Course {id: $course_id})-[:CONTAINS]->(concept:Concept)
            WHERE concept.week = $week_number
            OPTIONAL MATCH (prerequisite:Concept)-[*1..2]->(concept)
            WHERE prerequisite IS NULL OR ((course)-[:CONTAINS]->(prerequisite) AND prerequisite.week = $week_number)
            RETURN concept, collect(DISTINCT prerequisite) AS prerequisites
            ORDER BY concept.name
            LIMIT 50
            """,
            course_id=course_id,
            week_number=week_number,
        )
        return await result.data()

    @staticmethod
    def _resolve_embedding_device() -> str:
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"
