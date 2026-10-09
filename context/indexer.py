"""
Indexer — orchestrates the full indexing pipeline.

Flow for a full index run:
  connector.list_documents()
    → fetch each document
    → chunk_document()
    → extract_graph()
    → embedder.embed_batch()
    → store.upsert()
    → write memory/*.md graph notes

Flow for an incremental run:
  connector.list_documents()          ← detect deleted files
    → store.delete() for removed files
  connector.get_changes_since(last_indexed)
    → same as above for changed/new files only

The indexer is source-agnostic — it works with any combination of
connectors, embedders, and stores.

State file format
─────────────────
.index/index-state.json stores per-source state:
  {
    "<source_id>": {
      "last_indexed": "<ISO timestamp>",
      "git_sha": "<HEAD SHA or null>",
      "content_hashes": { "<meta.id>": "<sha256>" },
      "doc_chunks": {
        "<meta.id>": ["<chunk_doc_id_0>", "<chunk_doc_id_1>", ...]
      }
    }
  }

git_sha enables a coarse-grained skip: if the source's git HEAD hasn't
changed since the last run, the entire source is skipped (zero Qdrant
calls). content_hashes enable fine-grained skip: files that pass the
mtime filter but have identical content are not re-embedded.

The doc_chunks map lets the indexer delete all chunks belonging to a
source file that was removed since the last run. Old state files that
stored only a timestamp string are normalised automatically.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from context.connectors.base import SourceConnector, DocumentMetadata
from context.embedders.base import Embedder
from context.stores.base import VectorStore
from context.chunker import chunk_document, chunks_to_documents
from context.graph_extractor import extract_graph, graph_to_metadata, graph_to_markdown
from context.models import Document, DocumentType

EMBED_BATCH_SIZE = 128
MAX_FETCH_WORKERS = 16
STATE_FILE        = ".index/index-state.json"


def _detect_doc_type(path: str, language: str) -> DocumentType:
    if "test" in path.lower():
        return DocumentType.TEST
    if language == "markdown":
        return DocumentType.MARKDOWN
    if language in {"yaml", "json", "toml"}:
        return DocumentType.CONFIG
    if language in {"python", "javascript", "typescript", "java", "go"}:
        return DocumentType.CODE
    return DocumentType.UNKNOWN


class Indexer:
    """
    Orchestrates the full index pipeline across one or more source connectors.
    Supports incremental runs that only process delta changes and purge
    chunks belonging to deleted files.
    """

    def __init__(
        self,
        connectors: list[SourceConnector],
        embedder:   Embedder,
        store:      VectorStore,
        memory_dir: str = "memory",
        state_file: str = STATE_FILE,
    ) -> None:
        self.connectors = connectors
        self.embedder   = embedder
        self.store      = store
        self.memory_dir = Path(memory_dir)
        self.state_file = Path(state_file)

    def run(self, force_full: bool = False) -> dict:
        """
        Run the indexer for all connectors.

        Parameters
        ----------
        force_full : if True, re-index everything even if incremental is possible

        Returns a summary dict with counts per connector.
        """
        summary: dict = {}
        state = self._load_state()

        for connector in self.connectors:
            source_id = connector.get_identifier()
            print(f"\n  Indexing source: {source_id}")

            ok, msg = connector.health_check()
            if not ok:
                print(f"  ⚠️  Health check failed: {msg} — skipping")
                summary[source_id] = {"status": "skipped", "reason": msg}
                continue

            src_state = self._normalize_src_state(state.get(source_id))

            # ── Layer 1: git SHA coarse check ─────────────────────────────────
            # If the connector exposes the current git HEAD and it matches
            # what we indexed last time, nothing in this source has changed.
            if not force_full and src_state.get("last_indexed"):
                get_sha = getattr(connector, "get_current_git_sha", None)
                if callable(get_sha):
                    current_sha = get_sha()
                    stored_sha  = src_state.get("git_sha")
                    if current_sha and current_sha == stored_sha:
                        short = current_sha[:8]
                        print(f"  No git changes since last index (SHA {short}) — skipping")
                        summary[source_id] = {"status": "skipped", "reason": f"git SHA unchanged ({short})"}
                        continue

            use_incremental = (
                not force_full
                and src_state.get("last_indexed") is not None
                and connector.supports_incremental()
            )

            deleted_count = 0
            if use_incremental:
                since = datetime.fromisoformat(src_state["last_indexed"])
                print(f"  Incremental sync since {since.isoformat()}")

                # ── Deletion detection ────────────────────────────────────────
                # list_documents() is also needed by get_changes_since on some
                # connectors, so cache it to avoid a second full directory scan.
                all_docs = connector.list_documents()
                current_ids = {d.id for d in all_docs}
                prev_chunk_map: dict[str, list[str]] = src_state.get("doc_chunks", {})
                deleted_meta_ids = set(prev_chunk_map.keys()) - current_ids

                if deleted_meta_ids:
                    chunk_ids_to_delete: list[str] = []
                    for mid in deleted_meta_ids:
                        chunk_ids_to_delete.extend(prev_chunk_map.get(mid, []))
                    self.store.delete(chunk_ids_to_delete)
                    deleted_count = len(deleted_meta_ids)
                    print(f"  Purged {deleted_count} deleted file(s) ({len(chunk_ids_to_delete)} chunks)")

                docs_meta = connector.get_changes_since(since)
            else:
                print("  Full index run")
                docs_meta = connector.list_documents()
                prev_chunk_map = {}
                deleted_meta_ids = set()

            print(f"  Found {len(docs_meta)} document(s) to index")
            result, new_chunk_map, new_hash_map = self._index_documents(
                connector, docs_meta, src_state=src_state,
            )
            result["deleted"] = deleted_count
            summary[source_id] = result

            # ── Merge and persist state ───────────────────────────────────────
            if use_incremental:
                # Keep unchanged entries, remove deleted, overlay changed/new
                merged_chunks = {
                    k: v for k, v in prev_chunk_map.items()
                    if k not in deleted_meta_ids
                }
                merged_chunks.update(new_chunk_map)

                merged_hashes = dict(src_state.get("content_hashes", {}))
                for mid in deleted_meta_ids:
                    merged_hashes.pop(mid, None)
                merged_hashes.update(new_hash_map)
            else:
                merged_chunks = new_chunk_map
                merged_hashes = new_hash_map

            # Capture current git SHA (if available) for Layer 1 next run
            get_sha = getattr(connector, "get_current_git_sha", None)
            current_git_sha = get_sha() if callable(get_sha) else None

            state[source_id] = {
                "last_indexed": datetime.now(timezone.utc).isoformat(),
                "git_sha": current_git_sha,
                "content_hashes": merged_hashes,
                "doc_chunks": merged_chunks,
            }
            self._save_state(state)

        return summary

    def _index_documents(
        self,
        connector: SourceConnector,
        docs_meta: list[DocumentMetadata],
        src_state: dict | None = None,
    ) -> tuple[dict, dict[str, list[str]], dict[str, str]]:
        """
        Fetch, chunk, embed, and store the given documents.

        Returns
        -------
        (summary_dict, doc_chunk_map, content_hash_map)
        doc_chunk_map   — maps meta.id → list of stored chunk Document IDs
        content_hash_map — maps meta.id → SHA-256 hex of file content
        """
        source_id = connector.get_identifier()
        indexed = skipped = failed = 0
        graph_notes: dict[str, str] = {}
        doc_chunk_map: dict[str, list[str]] = {}
        content_hash_map: dict[str, str] = {}
        pending_docs: list[Document] = []
        stored_hashes: dict[str, str] = (src_state or {}).get("content_hashes", {})
        prev_chunk_map: dict[str, list[str]] = (src_state or {}).get("doc_chunks", {})

        # ── Layer 2: content-hash fine check (serial, no I/O) ─────────────────
        to_fetch: list[DocumentMetadata] = []
        for meta in docs_meta:
            if meta.content_hash:
                content_hash_map[meta.id] = meta.content_hash
                if meta.content_hash == stored_hashes.get(meta.id):
                    if meta.id in prev_chunk_map:
                        doc_chunk_map[meta.id] = prev_chunk_map[meta.id]
                    skipped += 1
                    continue
            to_fetch.append(meta)

        def _process(meta: DocumentMetadata):
            content = connector.fetch_document(meta.id)
            doc_type = _detect_doc_type(meta.path, meta.language)
            if "memory" in source_id.lower():
                doc_type = DocumentType.MEMORY
            graph      = extract_graph(meta.path, content, meta.language)
            graph_meta = graph_to_metadata(graph)
            base_doc   = Document(
                id=f"{source_id}::{meta.id}",
                source_id=source_id,
                path=meta.path,
                content=content,
                doc_type=doc_type,
                language=meta.language,
                last_modified=meta.last_modified,
                metadata=graph_meta,
            )
            chunks     = chunk_document(path=meta.path, content=content, doc_type=doc_type, language=meta.language)
            chunk_docs = chunks_to_documents(chunks, base_doc)
            note       = None
            if graph.entities or graph.imports:
                note_path = f"patterns/graph/{meta.path.replace('/', '_')}.md"
                note = (note_path, graph_to_markdown(graph))
            return chunk_docs, note

        # ── Parallel fetch + chunk + graph-extract ────────────────────────────
        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
            future_to_meta = {executor.submit(_process, m): m for m in to_fetch}
            for future in as_completed(future_to_meta):
                meta = future_to_meta[future]
                try:
                    chunk_docs, note = future.result()
                except Exception as e:
                    print(f"    ⚠️  Failed to process {meta.path}: {e}")
                    failed += 1
                    continue

                doc_chunk_map[meta.id] = [d.id for d in chunk_docs]
                pending_docs.extend(chunk_docs)
                if note:
                    graph_notes[note[0]] = note[1]

                if len(pending_docs) >= EMBED_BATCH_SIZE:
                    self._embed_and_store(pending_docs)
                    indexed += len(pending_docs)
                    pending_docs = []

        if pending_docs:
            self._embed_and_store(pending_docs)
            indexed += len(pending_docs)

        self._write_graph_notes(graph_notes)
        print(f"  ✅ Indexed: {indexed} chunks | Skipped: {skipped} | Failed: {failed}")
        return {"indexed": indexed, "skipped": skipped, "failed": failed}, doc_chunk_map, content_hash_map

    def _embed_and_store(self, docs: list[Document]) -> None:
        texts      = [d.content for d in docs]
        embeddings = self.embedder.embed_batch(texts)
        for doc, emb in zip(docs, embeddings):
            doc.embedding = emb
        self.store.upsert(docs)

    def _write_graph_notes(self, notes: dict[str, str]) -> None:
        for rel_path, content in notes.items():
            full_path = self.memory_dir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)

    # ── State helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_src_state(raw) -> dict:
        """Accept both the old plain-string timestamp and the new dict format."""
        if isinstance(raw, str):
            return {"last_indexed": raw, "doc_chunks": {}}
        return raw or {}

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except Exception:
                pass
        return {}

    def _save_state(self, state: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2))
