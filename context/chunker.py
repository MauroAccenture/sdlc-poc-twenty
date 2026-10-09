"""
Chunker — splits raw document content into indexable chunks.

Code files are chunked differently from prose:
- Code: split on top-level function/class boundaries (semantic chunking)
- Prose: split on paragraph boundaries with overlap
- Config/small files: kept as a single chunk

Chunk size is tuned for the default embedding model (MiniLM, 256-token limit).
"""

import re
from dataclasses import dataclass

from context.models import Document, DocumentType

DEFAULT_CHUNK_SIZE    = 1500   # chars (~300-400 tokens for MiniLM)
DEFAULT_CHUNK_OVERLAP = 200    # chars of overlap between adjacent chunks


@dataclass
class Chunk:
    content:     str
    chunk_index: int
    start_line:  int = 0


def chunk_document(
    path: str,
    content: str,
    doc_type: DocumentType,
    language: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int    = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """
    Split content into chunks appropriate for its type.
    Returns a list of Chunk objects ready to be embedded.
    """
    if not content.strip():
        return []

    # Small files: keep as single chunk
    if len(content) <= chunk_size:
        return [Chunk(content=content.strip(), chunk_index=0)]

    if language in {"python", "javascript", "typescript", "java", "go"}:
        chunks = _chunk_code(content, language, chunk_size, overlap)
    else:
        chunks = _chunk_prose(content, chunk_size, overlap)

    return chunks if chunks else [Chunk(content=content[:chunk_size], chunk_index=0)]


def _chunk_code(content: str, language: str, chunk_size: int, overlap: int) -> list[Chunk]:
    """
    Split code on top-level definition boundaries.
    Falls back to line-based chunking if no boundaries found.
    """
    # Patterns for top-level definitions per language
    patterns = {
        "python":     r"^(class |def |async def )",
        "javascript": r"^(function |class |const |export )",
        "typescript": r"^(function |class |const |export |interface |type )",
        "java":       r"^(public |private |protected |class |interface )",
        "go":         r"^(func |type |var |const )",
    }
    pattern = patterns.get(language, r"^(class |def |function )")

    lines  = content.splitlines()
    chunks: list[Chunk] = []
    current_lines: list[str] = []
    current_start = 0

    for i, line in enumerate(lines):
        # New top-level definition — flush current chunk if big enough
        if re.match(pattern, line) and current_lines and len("\n".join(current_lines)) > chunk_size // 3:
            chunk_text = "\n".join(current_lines).strip()
            if chunk_text:
                chunks.append(Chunk(content=chunk_text, chunk_index=len(chunks), start_line=current_start))
            # Overlap: carry last few lines into next chunk
            overlap_lines = current_lines[-5:] if len(current_lines) > 5 else []
            current_lines = overlap_lines + [line]
            current_start = max(0, i - len(overlap_lines))
        else:
            current_lines.append(line)

        # Flush if chunk is getting too large
        if len("\n".join(current_lines)) >= chunk_size:
            chunk_text = "\n".join(current_lines).strip()
            if chunk_text:
                chunks.append(Chunk(content=chunk_text, chunk_index=len(chunks), start_line=current_start))
            current_lines = current_lines[-5:]
            current_start = max(0, i - 4)

    # Flush remainder
    if current_lines:
        chunk_text = "\n".join(current_lines).strip()
        if chunk_text:
            chunks.append(Chunk(content=chunk_text, chunk_index=len(chunks), start_line=current_start))

    return chunks or [Chunk(content=content[:chunk_size], chunk_index=0)]


def _chunk_prose(content: str, chunk_size: int, overlap: int) -> list[Chunk]:
    """Split prose (markdown, docs) on paragraph boundaries."""
    paragraphs = re.split(r"\n\s*\n", content)
    chunks:  list[Chunk] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 > chunk_size and current:
            chunks.append(Chunk(content=current.strip(), chunk_index=len(chunks)))
            # Overlap: keep last paragraph in next chunk
            last_para = current.rsplit("\n\n", 1)[-1] if "\n\n" in current else ""
            current = last_para + "\n\n" + para if last_para else para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(Chunk(content=current.strip(), chunk_index=len(chunks)))

    return chunks


def chunks_to_documents(
    chunks: list[Chunk],
    source_document: Document,
) -> list[Document]:
    """Convert Chunk objects into Document objects ready for indexing."""
    total = len(chunks)
    return [
        Document(
            id=f"{source_document.id}::chunk{c.chunk_index}",
            source_id=source_document.source_id,
            path=source_document.path,
            content=c.content,
            doc_type=source_document.doc_type,
            language=source_document.language,
            last_modified=source_document.last_modified,
            chunk_index=c.chunk_index,
            total_chunks=total,
            metadata={
                **source_document.metadata,
                "start_line": c.start_line,
            },
        )
        for c in chunks
    ]
