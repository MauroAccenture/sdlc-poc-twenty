"""
Retriever — implements search_code() and recall() used by agents as tools.

search_code() : semantic search over the indexed codebase
recall()      : search over the project memory vault (cross-run knowledge)

Both are called via the tool-use API — Claude decides when to call them
and what query to use. The retriever formats results into readable text
that Claude can reason over.
"""

from pathlib import Path

from context.embedders.base import Embedder
from context.stores.base import VectorStore
from context.models import DocumentType


class Retriever:

    def __init__(
        self,
        store:      VectorStore,
        embedder:   Embedder,
        memory_dir: str = "memory",
    ) -> None:
        self.store      = store
        self.embedder   = embedder
        self.memory_dir = Path(memory_dir)

    def search_code(
        self,
        query: str,
        top_k: int = 5,
        language: str | None = None,
        doc_type: str | None = None,
        source_id: str | None = None,
    ) -> str:
        """
        Semantic search over the indexed codebase.

        Returns formatted text ready for Claude to read — file paths,
        relevance scores, and the relevant content chunk.

        Parameters
        ----------
        query     : natural language query, e.g. "how is JWT validated"
        top_k     : number of results (default 5)
        language  : optional filter, e.g. "python"
        doc_type  : optional filter, e.g. "code", "test", "config"
        source_id : optional filter to a specific source connector
        """
        query_vector = self.embedder.embed_one(query)

        filters: dict = {}
        if language:
            filters["language"] = language
        if doc_type:
            filters["doc_type"] = doc_type
        if source_id:
            filters["source_id"] = source_id

        results = self.store.search(
            query_vector=query_vector,
            top_k=top_k,
            filters=filters or None,
        )

        if not results:
            return f"No results found for query: '{query}'"

        lines = [f"Search results for: '{query}'\n"]
        for i, result in enumerate(results, 1):
            doc   = result.document
            score = result.score
            chunk_info = (
                f" (chunk {doc.chunk_index + 1}/{doc.total_chunks})"
                if doc.total_chunks > 1 else ""
            )
            lines.append(
                f"[{i}] {doc.path}{chunk_info} — relevance: {score:.2f}\n"
                f"    Source: {doc.source_id}\n"
                f"    Language: {doc.language}\n"
                f"```\n{doc.content[:800]}{'...' if len(doc.content) > 800 else ''}\n```\n"
            )

        return "\n".join(lines)

    def recall(self, query: str, top_k: int = 3) -> str:
        """
        Search the project memory vault for cross-run knowledge.

        Searches both:
        1. The vector index (for memory documents indexed as DocumentType.MEMORY)
        2. The raw memory/*.md files (for recency and exact matches)

        Returns formatted text with relevant memory content.
        """
        results_text: list[str] = []

        # 1. Vector search over memory documents
        query_vector = self.embedder.embed_one(query)
        results = self.store.search(
            query_vector=query_vector,
            top_k=top_k,
            filters={"doc_type": DocumentType.MEMORY.value},
        )

        if results:
            results_text.append(f"Relevant project memory for: '{query}'\n")
            for i, result in enumerate(results, 1):
                doc = result.document
                results_text.append(
                    f"[{i}] {doc.path} — relevance: {result.score:.2f}\n"
                    f"{doc.content[:600]}{'...' if len(doc.content) > 600 else ''}\n"
                )

        # 2. Scan recent run notes (last 5) for exact keyword matches
        runs_dir = self.memory_dir / "runs"
        if runs_dir.exists():
            run_files = sorted(runs_dir.glob("*.md"), reverse=True)[:5]
            query_lower = query.lower()
            for run_file in run_files:
                content = run_file.read_text(errors="replace")
                if query_lower in content.lower():
                    results_text.append(
                        f"\nFrom run log {run_file.name}:\n"
                        f"{self._extract_relevant_paragraph(content, query_lower)}\n"
                    )

        if not results_text:
            return f"No memory found relevant to: '{query}'"

        return "\n".join(results_text)

    def _extract_relevant_paragraph(self, text: str, query: str) -> str:
        """Extract the paragraph containing the query term."""
        paragraphs = text.split("\n\n")
        for para in paragraphs:
            if query in para.lower():
                return para.strip()[:400]
        return text[:400]
