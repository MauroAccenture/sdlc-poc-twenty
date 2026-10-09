"""
Local filesystem connector.

Indexes files from a local directory. Primarily used for:
- Testing the pipeline without a remote source
- Indexing the pipeline repo itself (memory/, sdlc/ artifacts)
- Local development

Configuration (AGENTS.md)
─────────────────────────
sources:
  - type: local
    path: ./app
    include_extensions: [.py, .md]
    exclude_dirs: [__pycache__, .git, .venv]
"""

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from context.connectors.base import DocumentMetadata, SourceConnector

DEFAULT_EXTENSIONS = {".py", ".js", ".ts", ".md", ".yaml", ".yml", ".json", ".txt"}
DEFAULT_EXCLUDE    = {"__pycache__", ".git", ".github", "node_modules", ".venv", "venv"}


class LocalConnector(SourceConnector):
    """Indexes files from a local directory tree."""

    def __init__(
        self,
        path: str,
        include_extensions: set[str] | None = None,
        exclude_dirs: set[str] | None = None,
        max_file_size_kb: int = 100,
    ) -> None:
        self.root = Path(path).resolve()
        self.include_extensions = include_extensions or DEFAULT_EXTENSIONS
        self.exclude_dirs       = exclude_dirs or DEFAULT_EXCLUDE
        self.max_file_size_bytes = max_file_size_kb * 1024

    def get_identifier(self) -> str:
        return f"local::{self.root}"

    def get_current_git_sha(self) -> str | None:
        """Return HEAD SHA if self.root is inside a git repo, else None."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def health_check(self) -> tuple[bool, str]:
        if not self.root.exists():
            return False, f"Path does not exist: {self.root}"
        if not self.root.is_dir():
            return False, f"Path is not a directory: {self.root}"
        return True, f"Local path accessible: {self.root}"

    def list_documents(self) -> list[DocumentMetadata]:
        results: list[DocumentMetadata] = []
        for entry in self.root.rglob("*"):
            if not entry.is_file():
                continue
            if any(p in self.exclude_dirs for p in entry.parts):
                continue
            if entry.suffix.lower() not in self.include_extensions:
                continue
            stat = entry.stat()
            if stat.st_size > self.max_file_size_bytes:
                continue

            rel = str(entry.relative_to(self.root))
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            file_bytes = entry.read_bytes()
            content_hash = hashlib.sha256(file_bytes).hexdigest()
            results.append(DocumentMetadata(
                id=rel,
                path=rel,
                last_modified=mtime,
                size_bytes=stat.st_size,
                language=self._detect_language(entry.suffix),
                content_hash=content_hash,
            ))
        return results

    def fetch_document(self, document_id: str) -> str:
        path = self.root / document_id
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return path.read_text(errors="replace")

    def get_changes_since(self, since: datetime) -> list[DocumentMetadata]:
        return [
            d for d in self.list_documents()
            if d.last_modified and d.last_modified > since
        ]

    def supports_incremental(self) -> bool:
        return True

    def _detect_language(self, ext: str) -> str:
        return {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".md": "markdown", ".yaml": "yaml", ".yml": "yaml",
            ".json": "json", ".sh": "bash",
        }.get(ext.lower(), "unknown")
