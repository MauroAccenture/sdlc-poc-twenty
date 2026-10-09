"""
Tool definitions for the agentic SDLC pipeline.

Tool subsets per agent
──────────────────────
Orchestrator  : read_file, list_files, write_file, run_command
Designer      : read_file, list_files, write_file, search_code, recall
Coder         : read_file, list_files, write_file, run_command, search_code, recall
Code reviewer : read_file, list_files, write_file, search_code
Tester        : read_file, list_files, write_file, run_command, search_code
QA engineer   : read_file, list_files, write_file, run_command

Note: search_code and recall are injected by the Orchestrator at runtime
once the retriever is initialised. If the index is unavailable they fall
back to a plain message so agents can degrade gracefully.
"""

import os
import subprocess
from pathlib import Path

# Set by orchestrator.py before any agent runs
_retriever = None

REPO_ROOT = Path(__file__).parents[1]


def set_retriever(retriever) -> None:
    """Called by the Orchestrator once the retriever is ready."""
    global _retriever
    _retriever = retriever


# ── Tool implementations ──────────────────────────────────────────────────────

def read_file(path: str) -> str:
    abs_path = REPO_ROOT / path
    if not abs_path.exists():
        return f"ERROR: file not found: {path}"
    try:
        return abs_path.read_text(errors="replace")
    except OSError as e:
        return f"ERROR reading {path}: {e}"


def list_files(directory: str = ".", extensions: list[str] | None = None) -> str:
    abs_dir = REPO_ROOT / directory
    if not abs_dir.exists():
        return f"ERROR: directory not found: {directory}"

    exclude = {"__pycache__", ".git", ".github", "node_modules", "sdlc",
               ".venv", "venv", ".index", ".cache"}
    results: list[str] = []

    for entry in sorted(abs_dir.rglob("*")):
        rel   = entry.relative_to(REPO_ROOT)
        parts = rel.parts
        if any(p in exclude or p.startswith(".") for p in parts):
            continue
        if entry.is_file():
            if extensions is None or entry.suffix in extensions:
                results.append(str(rel))

    return "\n".join(results) if results else "(no files found)"


def write_file(path: str, content: str) -> str:
    # SDLC artifacts belong at the pipeline repo root, not inside the target app.
    # Silently correct the common agent mistake of prefixing sdlc/ with "app/".
    if path.startswith("app/sdlc/"):
        path = path[len("app/"):]
    abs_path = REPO_ROOT / path
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content)
        return f"OK: wrote {len(content)} chars to {path}"
    except OSError as e:
        return f"ERROR writing {path}: {e}"


def run_command(command: str, working_directory: str = ".") -> str:
    abs_cwd = REPO_ROOT / working_directory
    try:
        result = subprocess.run(
            command, shell=True, cwd=abs_cwd,
            capture_output=True, text=True, timeout=120,
        )
        output = result.stdout + result.stderr
        status = "EXIT 0" if result.returncode == 0 else f"EXIT {result.returncode}"
        return f"{status}\n{output[:8000]}"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 120s"
    except Exception as e:
        return f"ERROR running command: {e}"


def search_code(
    query: str,
    top_k: int = 5,
    language: str | None = None,
    doc_type: str | None = None,
) -> str:
    """Semantic search over the indexed codebase."""
    if _retriever is None:
        return (
            "Index not available — falling back to direct file reads. "
            "Use list_files() and read_file() to explore the codebase."
        )
    return _retriever.search_code(query, top_k=top_k, language=language, doc_type=doc_type)


def recall(query: str, top_k: int = 3) -> str:
    """Search persistent project memory for decisions and patterns from previous runs."""
    if _retriever is None:
        return "Memory not available for this run."
    return _retriever.recall(query, top_k=top_k)


# ── Tool schemas (Anthropic tool-use format) ─────────────────────────────────

READ_FILE = {
    "name": "read_file",
    "description": "Read the full content of a file from the repository. Path is relative to repo root.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path, e.g. 'app/routes/auth.py'"}
        },
        "required": ["path"],
    },
}

LIST_FILES = {
    "name": "list_files",
    "description": "List files in a directory (recursive). Omit extensions to list all.",
    "input_schema": {
        "type": "object",
        "properties": {
            "directory":  {"type": "string", "description": "Relative directory path, default '.'"},
            "extensions": {"type": "array", "items": {"type": "string"},
                           "description": "Filter by extensions e.g. ['.py', '.md']"},
        },
    },
}

WRITE_FILE = {
    "name": "write_file",
    "description": "Write or overwrite a file. Creates parent directories automatically.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to the pipeline repo root (NOT the target app subdirectory). "
                    "SDLC artifacts: 'sdlc/<file>.md'. "
                    "Target application source files: 'app/<path>'. "
                    "Never use 'app/sdlc/' — SDLC artifacts live at the repo root, not inside the app."
                ),
            },
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
}

RUN_COMMAND = {
    "name": "run_command",
    "description": "Run a shell command and return stdout + stderr.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command":           {"type": "string"},
            "working_directory": {"type": "string", "description": "Relative to repo root, default '.'"},
        },
        "required": ["command"],
    },
}

SEARCH_CODE = {
    "name": "search_code",
    "description": (
        "Semantic search over the indexed codebase. Use this when you need to find "
        "files or patterns related to a concept without knowing the exact file path. "
        "Examples: 'JWT validation middleware', 'error handling pattern', "
        "'database connection setup'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query":     {"type": "string", "description": "Natural language search query"},
            "top_k":     {"type": "integer", "description": "Number of results (default 5)"},
            "language":  {"type": "string",  "description": "Filter by language, e.g. 'python'"},
            "doc_type":  {"type": "string",
                          "description": "Filter by type: 'code', 'test', 'config', 'markdown'"},
        },
        "required": ["query"],
    },
}

RECALL = {
    "name": "recall",
    "description": (
        "Search persistent project memory for decisions, patterns, and context "
        "from previous pipeline runs. Use this before designing or implementing "
        "anything — previous runs may have already established conventions. "
        "Examples: 'authentication approach', 'API error format', 'database schema'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look up in project memory"},
            "top_k": {"type": "integer", "description": "Number of memory items to return (default 3)"},
        },
        "required": ["query"],
    },
}

# ── Tool dispatcher ───────────────────────────────────────────────────────────

TOOL_IMPLEMENTATIONS = {
    "read_file":   lambda inp: read_file(inp["path"]),
    "list_files":  lambda inp: list_files(inp.get("directory", "."), inp.get("extensions")),
    "write_file":  lambda inp: write_file(inp["path"], inp["content"]),
    "run_command": lambda inp: run_command(inp["command"], inp.get("working_directory", ".")),
    "search_code": lambda inp: search_code(
        inp["query"], inp.get("top_k", 5),
        inp.get("language"), inp.get("doc_type"),
    ),
    "recall":      lambda inp: recall(inp["query"], inp.get("top_k", 3)),
}


def dispatch(tool_name: str, tool_input: dict) -> str:
    impl = TOOL_IMPLEMENTATIONS.get(tool_name)
    if impl is None:
        return f"ERROR: unknown tool '{tool_name}'"
    try:
        return impl(tool_input)
    except Exception as e:
        return f"ERROR in {tool_name}: {e}"
