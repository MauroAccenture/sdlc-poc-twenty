"""
Azure AI Search vector store — STUB.

Uses Azure AI Search (formerly Cognitive Search) as the vector store.
Supports both pure vector search and hybrid search (vector + BM25 keyword),
which typically outperforms pure vector search on code retrieval.

Configuration (AGENTS.md)
─────────────────────────
context:
  store:
    provider: azure_search
    index_name: sdlc-codebase
    vector_field: embedding
    use_hybrid_search: true   # recommended — combines vector + BM25

Required secrets
────────────────
AZURE_SEARCH_ENDPOINT  — e.g. https://my-search.search.windows.net
AZURE_SEARCH_API_KEY   — Admin key from Azure portal

Implementation guide
────────────────────
pip install azure-search-documents>=11.4.0

Key differences from Chroma:
1. Index schema must be created explicitly (see _create_index_if_needed).
2. Filters use OData syntax: "language eq 'python'" not {"language": "python"}.
3. Hybrid search: pass both vectors AND text to get BM25 + vector fusion.
4. Pagination: search returns an iterator, not a list — use list() or iterate.

Index schema example:
  fields = [
      SimpleField(name="id",        type="Edm.String", key=True),
      SearchField(name="content",   type="Edm.String", searchable=True),
      SearchField(name="path",      type="Edm.String", filterable=True),
      SearchField(name="source_id", type="Edm.String", filterable=True),
      SearchField(name="language",  type="Edm.String", filterable=True),
      SearchField(name="doc_type",  type="Edm.String", filterable=True),
      SearchField(
          name="embedding",
          type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
          searchable=True,
          vector_search_dimensions=384,      # must match embedder dimension
          vector_search_profile_name="default-profile",
      ),
  ]

Hybrid search example:
  results = search_client.search(
      search_text=query_text,           # BM25 component
      vector_queries=[VectorizedQuery(
          vector=query_vector,           # vector component
          k_nearest_neighbors=top_k,
          fields="embedding",
      )],
      filter=odata_filter,
      top=top_k,
  )

References
──────────
SDK docs:    https://learn.microsoft.com/en-us/python/api/azure-search-documents
Vector search: https://learn.microsoft.com/en-us/azure/search/vector-search-overview
Hybrid search: https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview
"""

import os
from context.models import Document, IndexStats, SearchResult
from context.stores.base import VectorStore


class AzureSearchStore(VectorStore):
    """Azure AI Search vector store. NOT YET IMPLEMENTED."""

    def __init__(
        self,
        index_name: str = "sdlc-codebase",
        vector_field: str = "embedding",
        use_hybrid_search: bool = True,
        endpoint: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.index_name         = index_name
        self.vector_field       = vector_field
        self.use_hybrid_search  = use_hybrid_search
        self.endpoint           = endpoint or os.environ.get("AZURE_SEARCH_ENDPOINT", "")
        self.api_key            = api_key  or os.environ.get("AZURE_SEARCH_API_KEY", "")

    def upsert(self, documents: list[Document]) -> None:
        raise NotImplementedError(
            "AzureSearchStore is not yet implemented. "
            "See module docstring for implementation guide."
        )

    def search(self, query_vector, top_k=5, filters=None) -> list[SearchResult]:
        raise NotImplementedError("AzureSearchStore is not yet implemented.")

    def delete(self, document_ids: list[str]) -> None:
        raise NotImplementedError("AzureSearchStore is not yet implemented.")

    def get_by_id(self, document_id: str) -> Document | None:
        raise NotImplementedError("AzureSearchStore is not yet implemented.")

    def get_stats(self) -> IndexStats:
        raise NotImplementedError("AzureSearchStore is not yet implemented.")
