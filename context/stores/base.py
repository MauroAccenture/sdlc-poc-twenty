"""
VectorStore — abstract base class for all vector store backends.

The store is responsible for persisting document embeddings and serving
semantic similarity queries. It is completely independent of the embedder
(which produces the vectors) and the connectors (which source the content).

Implementing a new store
────────────────────────
1. Subclass VectorStore.
2. Implement all five abstract methods.
3. Register in context/stores/__init__.py.
4. Set store.provider in AGENTS.md.

Switching stores
────────────────
If you switch from Chroma (384-dim) to Azure AI Search configured for
ada-002 (1536-dim), you must also switch the embedder and rebuild the
index. The store does not validate dimension compatibility at runtime —
the indexer does this on first run.
"""

from abc import ABC, abstractmethod
from context.models import Document, SearchResult, IndexStats


class VectorStore(ABC):

    @abstractmethod
    def upsert(self, documents: list[Document]) -> None:
        """
        Insert or update documents in the index.

        Documents with the same id are overwritten. Documents must have
        their embedding field populated before calling this method.

        Parameters
        ----------
        documents : list of Document objects with embeddings set
        """

    @abstractmethod
    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        """
        Semantic similarity search.

        Parameters
        ----------
        query_vector : embedded query vector (same dimension as stored vectors)
        top_k        : number of results to return
        filters      : optional metadata filters, e.g. {"language": "python"}
                       Filter keys correspond to Document.metadata keys plus
                       the top-level fields: source_id, path, doc_type, language.

        Returns
        -------
        List of SearchResult ordered by descending relevance score.
        """

    @abstractmethod
    def delete(self, document_ids: list[str]) -> None:
        """
        Remove documents by ID.

        Used during incremental sync to remove deleted/renamed files.
        Silently ignores IDs that do not exist.
        """

    @abstractmethod
    def get_by_id(self, document_id: str) -> Document | None:
        """
        Fetch a single document by its exact ID.

        Returns None if not found. Used for graph traversal — following
        entity relationships discovered during indexing.
        """

    @abstractmethod
    def get_stats(self) -> IndexStats:
        """
        Return summary statistics about the index.

        Called by the Orchestrator at startup to display index health
        and decide whether a re-index is needed.
        """

    def search_by_source(
        self,
        query_vector: list[float],
        source_id: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """
        Search within a single source (convenience wrapper).
        Filters by source_id metadata field.
        """
        return self.search(query_vector, top_k=top_k, filters={"source_id": source_id})

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"
