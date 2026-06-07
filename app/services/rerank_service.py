from typing import Any

import torch
from sentence_transformers import CrossEncoder


class RerankService:
    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
    ) -> None:
        self.model = CrossEncoder(model_name, device=self._resolve_device())

    def rerank(self, query: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not chunks:
            return []

        pairs = [(query, str(chunk.get("text", ""))) for chunk in chunks]
        scores = self.model.predict(pairs)

        ranked_chunks: list[dict[str, Any]] = []
        for chunk, score in zip(chunks, scores, strict=True):
            ranked_chunk = dict(chunk)
            ranked_chunk["rerank_score"] = float(score)
            ranked_chunks.append(ranked_chunk)

        return sorted(
            ranked_chunks,
            key=lambda chunk: float(chunk["rerank_score"]),
            reverse=True,
        )

    @staticmethod
    def _resolve_device() -> str:
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"
