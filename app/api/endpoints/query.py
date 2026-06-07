import asyncio
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.services.rag_service import RetrievalService
from app.services.rerank_service import RerankService
from app.services.synthesis_service import SynthesisService


router = APIRouter(prefix="/api/v1", tags=["query"])


class QueryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(..., min_length=1)
    course_id: str = Field(..., min_length=1)


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]]
    graph_context: list[dict[str, Any]]


@lru_cache
def get_retrieval_service() -> RetrievalService:
    return RetrievalService()


@lru_cache
def get_rerank_service() -> RerankService:
    return RerankService()


@lru_cache
def get_synthesis_service() -> SynthesisService:
    return SynthesisService()


@router.post("/query", response_model=QueryResponse)
async def query_conceptgraph(
    request: QueryRequest,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    rerank_service: RerankService = Depends(get_rerank_service),
    synthesis_service: SynthesisService = Depends(get_synthesis_service),
) -> QueryResponse:
    retrieval_result = await retrieval_service.retrieve(
        question=request.question,
        course_id=request.course_id,
    )
    ranked_chunks = await asyncio.to_thread(
        rerank_service.rerank,
        request.question,
        retrieval_result["chunks"],
    )
    answer = await synthesis_service.synthesize(
        question=request.question,
        graph_context=retrieval_result["graph_context"],
        ranked_chunks=ranked_chunks,
    )

    return QueryResponse(
        answer=answer,
        sources=ranked_chunks[:4],
        graph_context=retrieval_result["graph_context"],
    )
