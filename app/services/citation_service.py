from typing import Any


def build_sources(chunks: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str]] = set()
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        passage = " ".join(str(chunk.get("text", "")).split())
        page = metadata.get("page_number") if isinstance(metadata.get("page_number"), int) else None
        document_id = str(metadata.get("upload_id", ""))
        key = (document_id, page, passage[:240])
        if not passage or key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "source_id": f"source-{len(sources) + 1}",
                "document_id": document_id,
                "document_name": str(metadata.get("document_name") or "Course PDF"),
                "page_number": page,
                "section_heading": str(metadata.get("section_heading") or "") or None,
                "supporting_passage": passage[:900],
                "source_type": "pdf",
                "metadata": {
                    "retrieval_score": chunk.get("rerank_score", chunk.get("score")),
                    "upload_id": document_id,
                    "page_number": page,
                },
            }
        )
        if len(sources) >= limit:
            break
    return sources
