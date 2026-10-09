"""
GitHub source connector.

Fetches files from a remote GitHub repository via the GitHub REST API.
No local clone required — files are fetched on demand.

Configuration (claude.md)
─────────────────────────
sources:
  - type: github
    repo: myorg/myrepo          # required
    branch: main                # default: main
    include_paths:              # optional: only index these paths
      - app/
      - docs/
    exclude_paths:              # optional: skip these paths
      - app/tests/
      - .github/
    include_extensions:         # optional: only index these file types
      - .py
      - .md
      - .yaml
    max_file_size_kb: 100       # default: 100

Required secrets
────────────────
GITHUB_TOKEN — a personal access token or GitHub App token with
               repo read access. Set as a GitHub Actions secret.
               For public repos, a token is still recommended to
               avoid rate limits (60 req/hr unauthenticated vs
               5000 req/hr authenticated).
"""

import base64
import os
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json

from context.connectors.base import DocumentMetadata, SourceConnector

GITHUB_API = "https://api.github.com"

# File extensions considered indexable by default
DEFAULT_EXTENSIONS = {
    ".py", ".js", ".ts", ".java", ".go", ".rb", ".rs", ".cs",
    ".md", ".rst", ".txt",
    ".yaml", ".yml", ".toml", ".json",
    ".sh", ".dockerfile", ".env.example",
}


class GitHubConnector(SourceConnector):
    """
    Indexes a remote GitHub repository via the REST API.

    Fetches the full file tree once via the Git Trees API (single request),
    then fetches individual file contents on demand. Much more efficient
    than recursive directory listing.
    """

    def __init__(
        self,
        repo: str,
        branch: str = "main",
        token: str | None = None,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        include_extensions: set[str] | None = None,
        max_file_size_kb: int = 100,
    ) -> None:
        """
        Parameters
        ----------
        repo               : "owner/repo" format, e.g. "myorg/my-app"
        branch             : branch or tag to index, default "main"
        token              : GitHub personal access token. Falls back to
                             GITHUB_TOKEN environment variable.
        include_paths      : if set, only index files under these paths
        exclude_paths      : skip files under these paths
        include_extensions : if set, override DEFAULT_EXTENSIONS
        max_file_size_kb   : skip files larger than this
        """
        self.repo              = repo
        self.branch            = branch
        self.token             = token or os.environ.get("GITHUB_TOKEN", "")
        self.include_paths     = include_paths or []
        self.exclude_paths     = exclude_paths or []
        self.include_extensions = include_extensions or DEFAULT_EXTENSIONS
        self.max_file_size_bytes = max_file_size_kb * 1024
        self._tree_cache: list[dict] | None = None

    def get_identifier(self) -> str:
        return f"github::{self.repo}@{self.branch}"

    def health_check(self) -> tuple[bool, str]:
        try:
            data = self._api_get(f"/repos/{self.repo}")
            return True, f"Connected to {data.get('full_name')} ({data.get('visibility', 'unknown')})"
        except HTTPError as e:
            if e.code == 401:
                return False, "Invalid or missing GITHUB_TOKEN"
            if e.code == 404:
                return False, f"Repository '{self.repo}' not found or not accessible"
            return False, f"GitHub API error: HTTP {e.code}"
        except URLError as e:
            return False, f"Network error: {e.reason}"

    def list_documents(self) -> list[DocumentMetadata]:
        """
        Fetch the full file tree via the Git Trees API (recursive).
        One API call returns all file paths and sizes — very efficient.
        """
        tree = self._get_tree()
        results: list[DocumentMetadata] = []

        for item in tree:
            if item.get("type") != "blob":
                continue

            path = item["path"]
            size = item.get("size", 0)

            if not self._should_index(path, size):
                continue

            results.append(DocumentMetadata(
                id=item["sha"],
                path=path,
                last_modified=None,   # tree API doesn't return dates
                size_bytes=size,
                language=self._detect_language(path),
                url=f"https://github.com/{self.repo}/blob/{self.branch}/{path}",
            ))

        return results

    def fetch_document(self, document_id: str) -> str:
        """
        Fetch a file's content by its Git blob SHA.
        Returns decoded UTF-8 text.
        """
        data = self._api_get(f"/repos/{self.repo}/git/blobs/{document_id}")
        encoded = data.get("content", "")
        encoding = data.get("encoding", "base64")

        if encoding == "base64":
            raw = base64.b64decode(encoded.replace("\n", ""))
            return raw.decode("utf-8", errors="replace")
        return encoded

    def get_file_content_at_path(self, path: str) -> str:
        """
        Fetch a file by path directly — more efficient than base class default.
        Uses the Contents API which accepts a path directly.
        """
        try:
            data = self._api_get(
                f"/repos/{self.repo}/contents/{path}",
                params={"ref": self.branch},
            )
            encoded = data.get("content", "")
            raw = base64.b64decode(encoded.replace("\n", ""))
            return raw.decode("utf-8", errors="replace")
        except HTTPError as e:
            if e.code == 404:
                raise FileNotFoundError(f"File not found: {path} in {self.repo}@{self.branch}")
            raise

    def get_changes_since(self, since: datetime) -> list[DocumentMetadata]:
        """
        Return files modified since `since` by querying the commits API.
        Lists commits since the timestamp and collects changed files.
        """
        since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        commits = self._api_get(
            f"/repos/{self.repo}/commits",
            params={"sha": self.branch, "since": since_iso},
        )

        changed_paths: set[str] = set()
        for commit in commits:
            sha = commit["sha"]
            detail = self._api_get(f"/repos/{self.repo}/commits/{sha}")
            for f in detail.get("files", []):
                changed_paths.add(f["filename"])

        all_docs = {d.path: d for d in self.list_documents()}
        return [all_docs[p] for p in changed_paths if p in all_docs]

    def supports_incremental(self) -> bool:
        return True

    # ── Private helpers ──────────────────────────────────────────────────────

    def _get_tree(self) -> list[dict]:
        """Fetch and cache the recursive file tree."""
        if self._tree_cache is not None:
            return self._tree_cache

        # Get the SHA of the branch HEAD
        ref_data = self._api_get(f"/repos/{self.repo}/git/ref/heads/{self.branch}")
        commit_sha = ref_data["object"]["sha"]

        # Get the commit to find the tree SHA
        commit_data = self._api_get(f"/repos/{self.repo}/git/commits/{commit_sha}")
        tree_sha = commit_data["tree"]["sha"]

        # Fetch the full recursive tree
        tree_data = self._api_get(
            f"/repos/{self.repo}/git/trees/{tree_sha}",
            params={"recursive": "1"},
        )
        self._tree_cache = tree_data.get("tree", [])
        return self._tree_cache

    def _should_index(self, path: str, size: int) -> bool:
        """Apply all inclusion/exclusion filters."""
        import os as _os
        ext = _os.path.splitext(path)[1].lower()

        if ext not in self.include_extensions:
            return False
        if size > self.max_file_size_bytes:
            return False
        if self.include_paths and not any(path.startswith(p) for p in self.include_paths):
            return False
        if any(path.startswith(p) for p in self.exclude_paths):
            return False
        return True

    def _detect_language(self, path: str) -> str:
        import os as _os
        ext = _os.path.splitext(path)[1].lower()
        return {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".java": "java", ".go": "go", ".rb": "ruby", ".rs": "rust",
            ".cs": "csharp", ".md": "markdown", ".yaml": "yaml",
            ".yml": "yaml", ".json": "json", ".sh": "bash",
        }.get(ext, "unknown")

    def _api_get(self, endpoint: str, params: dict | None = None) -> dict | list:
        """Make an authenticated GET request to the GitHub API."""
        url = f"{GITHUB_API}{endpoint}"
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = Request(url, headers=headers)
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
