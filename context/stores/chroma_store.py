"""
Chroma vector store — local, free, zero infrastructure.

Chroma runs in-process and persists to a local directory. No server,
no Docker, no cloud account required. The index survives between runs
as long as the persist_directory exists.

The .index/ directory is gitignored — it is rebuilt from source on
first run in a new environment (e.g. fresh CI runner). For persistent
cross-CI indexing, either cache .index/ in GitHub Actions or switch to
a cloud store (Azure AI Search, Qdrant Cloud).

Configuration (AGENTS.md)
─────────────────────────
context:
  store:
    provider: chroma
    persist_directory: .index/chroma   # default
    collection_name: sdlc-codebase     # default
"""

from datetime import datetime, timezone

from context.models import Document, DocumentType, IndexStats, SearchResult
from context.stores.base import VectorStore


class ChromaStore(VectorStore):
    """
    Chroma-backed vector store.
    Lazily initialises the Chroma client on first use.
    """

    def __init__(
        self,
        persist_directory: str = ".index/chroma",
        collection_name: str = "sdlc-codebase",
    ) -> None:
        self.persist_directory = persist_directory
        self.collection_name   = collection_name
        self._client     = None
        self._collection = None

    def _get_collection(self):
        if self._collection is None:
            try:
                import chromadb
            except ImportError:
                raise ImportError(
                    "chromadb is not installed. Run: pip install chromadb"
                )
            self._client = chromadb.PersistentClient(path=self.persist_directory)
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def upsert(self, documents: list[Document]) -> None:
        if not documents:
            return
        col = self._get_collection()

        col.upsert(
            ids=[d.id for d in documents],
            embeddings=[d.embedding for d in documents],
            documents=[d.content for d in documents],
            metadatas=[self._to_metadata(d) for d in documents],
        )

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        col = self._get_collection()
        where = self._build_where(filters) if filters else None

        kwargs: dict = {"query_embeddings": [query_vector], "n_results": top_k}
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)

        output: list[SearchResult] = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta     = results["metadatas"][0][i]
            content  = results["documents"][0][i]
            distance = results["distances"][0][i]
            score    = 1.0 - distance   # cosine distance → similarity

            doc = self._from_metadata(doc_id, content, meta)
            output.append(SearchResult(document=doc, score=round(score, 4)))

        return output

    def delete(self, document_ids: list[str]) -> None:
        if not document_ids:
            return
        col = self._get_collection()
        col.delete(ids=document_ids)

    def get_by_id(self, document_id: str) -> Document | None:
        col = self._get_collection()
        result = col.get(ids=[document_id], include=["documents", "metadatas"])
        if not result["ids"]:
            return None
        meta    = result["metadatas"][0]
        content = result["documents"][0]
        return self._from_metadata(document_id, content, meta)

    def get_stats(self) -> IndexStats:
        col   = self._get_collection()
        count = col.count()
        meta  = col.get(include=["metadatas"])

        sources: set[str] = set()
        for m in meta.get("metadatas") or []:
            if m and m.get("source_id"):
                sources.add(m["source_id"])

        return IndexStats(
            total_documents=count,
            total_chunks=count,
            sources=sorted(sources),
            last_updated=datetime.now(timezone.utc),
        )

    # ── Metadata helpers ─────────────────────────────────────────────────────

    def _to_metadata(self, doc: Document) -> dict:
        """Flatten Document fields into Chroma metadata (strings/ints only)."""
        return {
            "source_id":    doc.source_id,
            "path":         doc.path,
            "doc_type":     doc.doc_type.value,
            "language":     doc.language,
            "chunk_index":  doc.chunk_index,
            "total_chunks": doc.total_chunks,
            "last_modified": doc.last_modified.isoformat() if doc.last_modified else "",
            **{k: str(v) for k, v in doc.metadata.items()},
        }

    def _from_metadata(self, doc_id: str, content: str, meta: dict) -> Document:
        last_mod = None
        if meta.get("last_modified"):
            try:
                last_mod = datetime.fromisoformat(meta["last_modified"])
            except ValueError:
                pass

        return Document(
            id=doc_id,
            source_id=meta.get("source_id", ""),
            path=meta.get("path", ""),
            content=content,
            doc_type=DocumentType(meta.get("doc_type", "unknown")),
            language=meta.get("language", ""),
            chunk_index=int(meta.get("chunk_index", 0)),
            total_chunks=int(meta.get("total_chunks", 1)),
            last_modified=last_mod,
            metadata={k: v for k, v in meta.items()
                      if k not in {"source_id", "path", "doc_type", "language",
                                   "chunk_index", "total_chunks", "last_modified"}},
        )

    def _build_where(self, filters: dict) -> dict:
        """Convert simple key=value filters to Chroma where clause."""
        if len(filters) == 1:
            k, v = next(iter(filters.items()))
            return {k: {"$eq": v}}
        return {"$and": [{k: {"$eq": v}} for k, v in filters.items()]}
