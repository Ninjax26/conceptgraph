from dataclasses import dataclass

import fitz
from langchain_text_splitters import RecursiveCharacterTextSplitter


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    id: str
    text: str
    metadata: dict[str, str | int]


class ParserService:
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50) -> None:
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=self._estimate_token_count,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    def extract_pages_from_bytes(self, content: bytes) -> list[tuple[int, str]]:
        pages: list[tuple[int, str]] = []
        with fitz.open(stream=content, filetype="pdf") as document:
            for page_index, page in enumerate(document, start=1):
                text = page.get_text("text").strip()
                if text:
                    pages.append((page_index, text))
        return pages

    def chunk_pages(
        self,
        pages: list[tuple[int, str]],
        document_id: str,
        upload_id: str,
        document_name: str = "",
    ) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        for page_index, text in pages:
            section_heading = next(
                (line.strip() for line in text.splitlines() if 3 <= len(line.strip()) <= 120),
                None,
            )
            raw_chunks = self.text_splitter.create_documents(
                    texts=[text],
                    metadatas=[
                        {
                            "document_id": document_id,
                            "upload_id": upload_id,
                            "document_name": document_name,
                            "page_number": page_index,
                            "section_heading": section_heading or "",
                        }
                    ],
                )

            for index, chunk in enumerate(raw_chunks):
                chunk_id = f"{upload_id}:{page_index}:{index}"
                chunks.append(
                        DocumentChunk(
                            id=chunk_id,
                            text=chunk.page_content,
                            metadata={
                                "chunk_id": chunk_id,
                                "chunk_index": index,
                                "document_id": document_id,
                                "upload_id": upload_id,
                                "document_name": document_name,
                                "page_number": page_index,
                                "section_heading": section_heading or "",
                            },
                        )
                )

        return chunks

    @staticmethod
    def _estimate_token_count(text: str) -> int:
        return len(text.split())
