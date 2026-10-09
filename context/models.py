"""
Shared data models used across connectors, stores, embedders, and retrievers.

These are plain dataclasses — no framework dependencies. Every layer speaks
this language so components are independently swappable.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DocumentType(str, Enum):
    CODE        = "code"
    MARKDOWN    = "markdown"
    CONFIG      = "config"
    TEST        = "test"
    MEMORY      = "memory"
    UNKNOWN     = "unknown"


@dataclass
class Document:
    """
    A single indexable unit of content.

    One source file typically produces multiple Documents after chunking —
    each chunk becomes one Document with the same source_id but a unique id.
    """
    id:           str                       # unique: "{source_id}::{path}::{chunk_index}"
    source_id:    str                       # connector identifier, e.g. "github::myorg/myrepo"
    path:         str                       # relative path within the source
    content:      str                       # raw text content of this chunk
    doc_type:     DocumentType = DocumentType.UNKNOWN
    language:     str          = ""         # e.g. "python", "markdown"
    metadata:     dict[str, Any] = field(default_factory=dict)
    embedding:    list[float]    = field(default_factory=list)
    last_modified: datetime | None = None
    chunk_index:  int  = 0
    total_chunks: int  = 1

    @property
    def is_chunked(self) -> bool:
        return self.total_chunks > 1

    def without_embedding(self) -> "Document":
        """Return a copy without the embedding vector (for logging/display)."""
        import dataclasses
        return dataclasses.replace(self, embedding=[])


@dataclass
class SearchResult:
    """A document returned by a vector search, with its relevance score."""
    document: Document
    score:    float        # 0.0–1.0, higher = more relevant

    def __repr__(self) -> str:
        return f"SearchResult(score={self.score:.3f}, path={self.document.path})"


@dataclass
class IndexStats:
    """Summary statistics about a vector store collection."""
    total_documents: int
    total_chunks:    int
    sources:         list[str]
    last_updated:    datetime | None
    size_bytes:      int = 0
