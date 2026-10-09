"""
Snapshot — builds a structured context summary once per pipeline run.

The snapshot is written to context/run-snapshot.md before any agent starts.
All agents read it via read_file("context/run-snapshot.md") instead of
crawling the source files independently — eliminating 3-5x redundant reads.

Contents:
  - Repository structure (file tree)
  - Tech stack summary
  - Key patterns (extracted from index metadata)
  - Recent run history (from memory/)
  - Index health (what is indexed, when)
"""

from datetime import datetime, timezone
from pathlib import Path

from context.stores.base import VectorStore

SNAPSHOT_PATH = "context/run-snapshot.md"


def build_snapshot(
    store:       VectorStore,
    memory_dir:  str,
    intent:      str,
    config:      dict,
    greenfield:  bool = False,
) -> str:
    """
    Build the run snapshot and write it to context/run-snapshot.md.
    Returns the snapshot content.
    """
    memory_path = Path(memory_dir)
    stack       = config.get("pipeline", {}).get("context", {}).get("stack") or {}

    sections: list[str] = [
        f"# Run snapshot — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Current intent",
        intent.strip(),
        "",
    ]

    if greenfield:
        sections += [
            "## Pipeline mode",
            "**GREENFIELD** — Building a new application from scratch.",
            "- There is no existing codebase. search_code() and recall() will return no results initially.",
            "- Do NOT waste tool calls searching for existing patterns to match — there are none.",
            "- Design and implement the COMPLETE initial project structure from zero.",
            "",
        ]

    # Index health
    if store is None:
        sections += ["## Codebase index", "- Index not initialised (RAG unavailable)", ""]
    else:
        try:
            stats = store.get_stats()
            sections += [
                "## Codebase index",
                f"- Documents indexed: {stats.total_chunks}",
                f"- Sources: {', '.join(stats.sources) or 'none'}",
                f"- Last updated: {stats.last_updated.isoformat() if stats.last_updated else 'unknown'}",
                "",
            ]
        except Exception as exc:
            import traceback
            print(f"  ⚠️  get_stats() failed: {exc}")
            traceback.print_exc()
            sections += ["## Codebase index", f"- Index stats unavailable: {exc}", ""]

    # Tech stack
    sections += [
        "## Tech stack",
        f"- Language: {stack.get('language', 'unknown')}",
        f"- Framework: {stack.get('framework', 'unknown')}",
        f"- Test framework: {stack.get('test_framework', 'unknown')}",
        f"- Database: {stack.get('database', 'unknown')}",
        f"- Auth: {stack.get('auth', 'unknown')}",
        "",
    ]

    # Recent run history
    runs_dir = memory_path / "runs"
    if runs_dir.exists():
        recent = sorted(runs_dir.glob("*.md"), reverse=True)[:3]
        if recent:
            sections += ["## Recent pipeline runs (last 3)"]
            for run_file in recent:
                first_line = run_file.read_text().splitlines()[0].lstrip("#").strip()
                sections.append(f"- [{run_file.stem}] {first_line}")
            sections.append("")

    # Architecture decisions
    adr_path = memory_path / "architecture-decisions.md"
    if adr_path.exists():
        sections += [
            "## Architecture decisions (summary)",
            adr_path.read_text()[:800] + ("..." if adr_path.stat().st_size > 800 else ""),
            "",
        ]

    # Key patterns
    patterns_dir = memory_path / "patterns"
    if patterns_dir.exists():
        pattern_files = list(patterns_dir.glob("*.md"))
        if pattern_files:
            sections += ["## Established patterns"]
            for pf in pattern_files[:5]:
                sections.append(f"- [[{pf.stem}]] — {pf.read_text().splitlines()[0].lstrip('#').strip()}")
            sections.append("")

    sections += [
        "## How to use this snapshot",
        "Read this file first via read_file('context/run-snapshot.md').",
        "Then use search_code() for specific questions about the codebase.",
        "Use recall() for project decisions and patterns from previous runs.",
        "Use read_file() when you need a specific file in full.",
    ]

    content = "\n".join(sections)

    # Write to disk
    Path(SNAPSHOT_PATH).parent.mkdir(exist_ok=True)
    Path(SNAPSHOT_PATH).write_text(content)

    return content
