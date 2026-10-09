"""
Qdrant Cloud vector store — remote, managed, production-ready.

Connects to a Qdrant Cloud cluster (or any self-hosted Qdrant instance)
over HTTPS using an API key. The collection is created automatically on
first use with cosine similarity and the configured vector size.

Configuration (AGENTS.md)
─────────────────────────
context:
  store:
    provider: qdrant
    collection_name: sdlc-codebase   # default
    vector_size: 384                 # must match embedder dimension

Required secrets
────────────────
QDRANT_URL     — e.g. https://abc123.us-east4-0.gcp.cloud.qdrant.io
QDRANT_API_KEY — API key from the Qdrant Cloud console

ID handling
───────────
Qdrant point IDs must be UUIDs or unsigned integers. Since our Document
IDs are arbitrary strings (e.g. "github::repo::path::0"), each string is
deterministically converted to a UUID5 and the original string is stored
in the payload under the reserved key "_id" for lossless round-tripping.
"""

import os
import uuid
from datetime import datetime, timezone

from context.models import Document, DocumentType, IndexStats, SearchResult
from context.stores.base import VectorStore

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL


def _to_point_id(doc_id: str) -> str:
    return str(uuid.uuid5(_NS, doc_id))


class QdrantStore(VectorStore):
    """
    Qdrant-backed vector store.
    Lazily initialises the client and creates the collection on first use.
    """

    def __init__(
        self,
        collection_name: str = "sdlc-codebase",
        vector_size: int = 384,
        url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.url = url or os.environ.get("QDRANT_URL", "")
        self.api_key = api_key or os.environ.get("QDRANT_API_KEY", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError:
                raise ImportError(
                    "qdrant-client is not installed. Run: pip install qdrant-client"
                )
            if not self.url:
                raise ValueError(
                    "QDRANT_URL is not set. "
                    "Export it or pass url= to QdrantStore."
                )
            self._client = QdrantClient(url=self.url, api_key=self.api_key or None)
            self._ensure_collection()
        return self._client

    # Fields used in search filters — each needs a keyword payload index
    # so Qdrant uses an inverted index rather than a full-collection scan.
    _INDEXED_FIELDS = ("doc_type", "language", "source_id")

    def _ensure_collection(self) -> None:
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        client = self._client
        existing = {c.name for c in client.get_collections().collections}
        if self.collection_name not in existing:
            client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=Distance.COSINE,
                ),
            )

        # Create keyword payload indexes for every filter field.
        # create_payload_index() is idempotent — safe to call on existing indexes.
        for field in self._INDEXED_FIELDS:
            try:
                client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as exc:
                print(f"  ⚠️  Could not create payload index for '{field}': {exc}")

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def upsert(self, documents: list[Document]) -> None:
        if not documents:
            return
        from qdrant_client.models import PointStruct

        client = self._get_client()
        points = [
            PointStruct(
                id=_to_point_id(d.id),
                vector=d.embedding,
                payload=self._to_payload(d),
            )
            for d in documents
        ]
        client.upsert(collection_name=self.collection_name, points=points)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        client = self._get_client()
        qdrant_filter = self._build_filter(filters) if filters else None

        # client.search() was removed in qdrant-client v2.0; use query_points()
        response = client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )

        return [
            SearchResult(
                document=self._from_payload(hit.payload),
                score=round(hit.score, 4),
            )
            for hit in response.points
        ]

    def delete(self, document_ids: list[str]) -> None:
        if not document_ids:
            return
        from qdrant_client.models import PointIdsList

        client = self._get_client()
        client.delete(
            collection_name=self.collection_name,
            points_selector=PointIdsList(
                points=[_to_point_id(did) for did in document_ids]
            ),
        )

    def get_by_id(self, document_id: str) -> Document | None:
        client = self._get_client()
        results = client.retrieve(
            collection_name=self.collection_name,
            ids=[_to_point_id(document_id)],
            with_payload=True,
            with_vectors=False,
        )
        if not results:
            return None
        return self._from_payload(results[0].payload)

    # Maximum number of points to scroll when collecting distinct source_ids.
    _STATS_SCROLL_CAP = 10_000

    def get_stats(self) -> IndexStats:
        client = self._get_client()
        info = client.get_collection(self.collection_name)

        # points_count is the authoritative field in qdrant-client >= 1.7;
        # vectors_count is only populated for named-vector collections and
        # returns None in standard single-vector collections.
        count = (
            getattr(info, "points_count", None)
            or getattr(info, "vectors_count", None)
            or 0
        )

        # Scroll to collect distinct source_ids, capped to avoid blocking on
        # very large collections.  This is display-only; accuracy past the cap
        # is not required.
        # qdrant-client <=1.x: scroll() returns (List[Record], Optional[PointId])
        # qdrant-client >=2.x: scroll() returns a ScrollResponse object
        sources: set[str] = set()
        offset = None
        seen = 0
        while seen < self._STATS_SCROLL_CAP:
            result = client.scroll(
                collection_name=self.collection_name,
                limit=1000,
                offset=offset,
                with_payload=["source_id"],
                with_vectors=False,
            )
            if isinstance(result, tuple):
                batch, offset = result
            else:
                batch  = result.points
                offset = getattr(result, "next_page_offset", None)

            for point in batch:
                sid = (point.payload or {}).get("source_id")
                if sid:
                    sources.add(sid)
            seen += len(batch)
            if offset is None or not batch:
                break

        return IndexStats(
            total_documents=count,
            total_chunks=count,
            sources=sorted(sources),
            last_updated=datetime.now(timezone.utc),
        )

    # ── Payload helpers ───────────────────────────────────────────────────────

    # Keys that must not be overwritten by doc.metadata entries.
    _RESERVED_PAYLOAD_KEYS = frozenset({
        "_id", "source_id", "path", "content", "doc_type",
        "language", "chunk_index", "total_chunks", "last_modified",
    })

    def _to_payload(self, doc: Document) -> dict:
        payload = {
            "_id":           doc.id,          # original string ID for round-trip
            "source_id":     doc.source_id,
            "path":          doc.path,
            "content":       doc.content,
            "doc_type":      doc.doc_type.value,
            "language":      doc.language,
            "chunk_index":   doc.chunk_index,
            "total_chunks":  doc.total_chunks,
            "last_modified": doc.last_modified.isoformat() if doc.last_modified else None,
        }
        # Merge metadata but never let it overwrite reserved structural keys.
        safe_meta = {k: v for k, v in doc.metadata.items()
                     if k not in self._RESERVED_PAYLOAD_KEYS}
        payload.update(safe_meta)
        return payload

    def _from_payload(self, payload: dict) -> Document:
        last_mod = None
        raw_ts = payload.get("last_modified")
        if raw_ts:
            try:
                last_mod = datetime.fromisoformat(raw_ts)
            except (ValueError, TypeError):
                pass

        return Document(
            id=payload.get("_id", ""),
            source_id=payload.get("source_id", ""),
            path=payload.get("path", ""),
            content=payload.get("content", ""),
            doc_type=DocumentType(payload.get("doc_type", "unknown")),
            language=payload.get("language", ""),
            chunk_index=int(payload.get("chunk_index", 0)),
            total_chunks=int(payload.get("total_chunks", 1)),
            last_modified=last_mod,
            metadata={k: v for k, v in payload.items()
                      if k not in self._RESERVED_PAYLOAD_KEYS},
        )

    def _build_filter(self, filters: dict):
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        conditions = [
            FieldCondition(key=k, match=MatchValue(value=v))
            for k, v in filters.items()
        ]
        return Filter(must=conditions)
