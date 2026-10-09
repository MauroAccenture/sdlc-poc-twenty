"""
SourceConnector — abstract base class for all content sources.

Every source that can be indexed (GitHub, SharePoint, Confluence, local
filesystem) implements this interface. The indexer only knows about this
abstraction — it never imports a concrete connector directly.

Implementing a new connector
────────────────────────────
1. Subclass SourceConnector.
2. Implement all abstract methods.
3. Register in context/connectors/__init__.py.
4. Add a config section in claude.md under sources:.
5. That's it — the indexer, agents, and retriever need no changes.

Contract
────────
- list_documents() must be idempotent and safe to call repeatedly.
- fetch_document() must return the full raw content, not a summary.
- get_changes_since() is used for incremental re-indexing. If not
  supported, raise NotImplementedError and the indexer will fall back
  to a full re-index.
- get_identifier() must be stable across runs — it is used as the
  source_id in Document and as the partition key in the vector store.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class DocumentMetadata:
    """
    Lightweight metadata returned by list_documents().
    The indexer uses this to decide what to fetch and index.
    """
    id:            str              # stable unique ID within this source
    path:          str              # human-readable path / title
    last_modified: datetime | None  # used for incremental sync
    size_bytes:    int   = 0
    language:      str   = ""       # hint: "python", "markdown", etc.
    url:           str   = ""       # optional: link back to source
    content_hash:  str | None = None  # SHA-256 of file bytes; set by connectors that support it


class SourceConnector(ABC):
    """
    Abstract base class for all content source connectors.

    Subclasses must implement the four abstract methods below.
    The optional methods (get_changes_since, supports_incremental)
    may be left as-is for sources that do not support incremental sync.
    """

    # ── Required ────────────────────────────────────────────────────────────

    @abstractmethod
    def get_identifier(self) -> str:
        """
        Return a stable, unique identifier for this source.

        Used as the source_id prefix in Document.id and as the partition
        key in the vector store. Must be stable across runs.

        Examples:
            "github::myorg/myrepo"
            "sharepoint::myorg/sites/Engineering"
            "local::/home/user/project"
        """

    @abstractmethod
    def list_documents(self) -> list[DocumentMetadata]:
        """
        Return metadata for all indexable documents in this source.

        Must be safe to call repeatedly. Should not fetch full content —
        that is done lazily by fetch_document().

        Returns an empty list if the source is empty or unreachable.
        """

    @abstractmethod
    def fetch_document(self, document_id: str) -> str:
        """
        Fetch the full raw content of a document by its ID.

        Parameters
        ----------
        document_id : the id field from DocumentMetadata

        Returns
        -------
        Raw text content of the document.

        Raises
        ------
        FileNotFoundError if the document does not exist.
        ConnectionError if the source is unreachable.
        """

    @abstractmethod
    def health_check(self) -> tuple[bool, str]:
        """
        Verify the source is reachable and credentials are valid.

        Returns
        -------
        (True, "ok") on success.
        (False, "<reason>") on failure.

        Called by the indexer before starting a full index run.
        """

    # ── Optional ────────────────────────────────────────────────────────────

    def get_changes_since(self, since: datetime) -> list[DocumentMetadata]:
        """
        Return metadata for documents modified since the given timestamp.

        Used for incremental re-indexing. If not implemented, the indexer
        will fall back to a full re-index on every run.

        The default implementation raises NotImplementedError to signal
        that incremental sync is not supported by this connector.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support incremental sync. "
            "The indexer will fall back to full re-index."
        )

    def supports_incremental(self) -> bool:
        """
        Return True if this connector implements get_changes_since().

        The indexer calls this before attempting incremental sync.
        """
        return False

    def get_file_content_at_path(self, path: str) -> str:
        """
        Convenience method: fetch a document by its path rather than its ID.

        Default implementation calls list_documents() to resolve the path
        to an ID, then calls fetch_document(). Connectors may override
        this for efficiency (e.g. GitHub can fetch by path directly).

        Raises FileNotFoundError if no document matches the path.
        """
        docs = self.list_documents()
        match = next((d for d in docs if d.path == path), None)
        if match is None:
            raise FileNotFoundError(
                f"No document found at path '{path}' in source '{self.get_identifier()}'"
            )
        return self.fetch_document(match.id)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.get_identifier()!r})"
