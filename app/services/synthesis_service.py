import asyncio
from typing import Any

from groq import Groq

from app.core.config import settings
from app.core.exceptions import LLMConfigurationError


class SynthesisService:
    def validate_provider_configured(self) -> None:
        provider = settings.llm_provider.lower()
        if provider == "groq" and not settings.groq_api_key:
            raise LLMConfigurationError("GROQ_API_KEY is required when LLM_PROVIDER=groq")
        if provider == "gemini" and not settings.gemini_api_key:
            raise LLMConfigurationError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")

    async def synthesize(
        self,
        question: str,
        graph_context: list[dict[str, Any]],
        ranked_chunks: list[dict[str, Any]],
    ) -> str:
        self.validate_provider_configured()
        top_chunks = ranked_chunks[:4]
        provider = settings.llm_provider.lower()
        if provider == "gemini":
            return await asyncio.to_thread(
                self._synthesize_with_gemini,
                question,
                graph_context,
                top_chunks,
            )
        if provider == "groq":
            return await asyncio.to_thread(
                self._synthesize_with_groq,
                question,
                graph_context,
                top_chunks,
            )
        raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")

    def _synthesize_with_groq(
        self,
        question: str,
        graph_context: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
    ) -> str:
        client = Groq(api_key=settings.groq_api_key)
        completion = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(question, graph_context, chunks)},
            ],
            temperature=0,
        )
        return completion.choices[0].message.content or ""

    def _synthesize_with_gemini(
        self,
        question: str,
        graph_context: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
    ) -> str:
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(
            [
                self._system_prompt(),
                self._user_prompt(question, graph_context, chunks),
            ],
            generation_config={"temperature": 0},
        )
        return response.text or ""

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are ConceptGraph, a syllabus-bounded academic assistant. Answer strictly "
            "from the provided textbook chunks and graph context. Do not use outside knowledge. "
            "If the answer cannot be confidently derived from the provided context, state: "
            "\"The answer is not present in the current syllabus boundaries.\" "
            "Cite chunk ids when using evidence."
        )

    @staticmethod
    def _user_prompt(
        question: str,
        graph_context: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
    ) -> str:
        chunk_context = "\n\n".join(
            (
                f"Chunk ID: {chunk.get('id')}\n"
                f"Score: {chunk.get('rerank_score', chunk.get('score'))}\n"
                f"Text:\n{chunk.get('text', '')}"
            )
            for chunk in chunks
        )
        return (
            f"Question:\n{question}\n\n"
            f"Graph context:\n{graph_context}\n\n"
            f"Textbook chunks:\n{chunk_context}"
        )
