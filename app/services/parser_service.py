from dataclasses import dataclass
from pathlib import Path

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

    def extract_text(self, file_path: str) -> str:
        pdf_path = Path(file_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        page_text: list[str] = []
        with fitz.open(pdf_path) as document:
            for page_index, page in enumerate(document, start=1):
                text = page.get_text("text").strip()
                if text:
                    page_text.append(f"[Page {page_index}]\n{text}")

        return "\n\n".join(page_text)

    def parse_and_chunk(self, file_path: str, document_id: str) -> list[DocumentChunk]:
        text = self.extract_text(file_path)
        raw_chunks = self.text_splitter.create_documents(
            texts=[text],
            metadatas=[{"document_id": document_id, "source_path": file_path}],
        )

        chunks: list[DocumentChunk] = []
        for index, chunk in enumerate(raw_chunks):
            chunk_id = f"{document_id}:{index}"
            chunks.append(
                DocumentChunk(
                    id=chunk_id,
                    text=chunk.page_content,
                    metadata={
                        "chunk_id": chunk_id,
                        "chunk_index": index,
                        "document_id": document_id,
                        "source_path": file_path,
                    },
                )
            )

        return chunks

    @staticmethod
    def _estimate_token_count(text: str) -> int:
        return len(text.split())
