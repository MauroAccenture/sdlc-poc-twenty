"""
Memory manager — persistent cross-run project knowledge.

Manages the memory/ vault: a folder of plain markdown files that accumulates
architectural decisions, established patterns, and run history across pipeline
runs. The vault is Obsidian-compatible — open it as a vault in Obsidian to
browse the knowledge graph visually.

File structure
──────────────
memory/
├── project-overview.md        written on first run, updated as needed
├── architecture-decisions.md  append-only log of decisions made by agents
├── patterns/
│   ├── api-conventions.md     REST conventions established in this project
│   ├── error-handling.md      how errors are handled across the codebase
│   └── graph/                 per-file entity graphs (written by indexer)
└── runs/
    ├── 2024-01-15-add-auth.md    one file per pipeline run
    └── 2024-01-22-add-tasks.md

Obsidian usage
──────────────
Open the memory/ folder as an Obsidian vault:
  File → Open Vault → select the memory/ directory

The graph view (Ctrl+G) shows connections between pattern notes,
architecture decisions, and run logs via [[wikilinks]].
"""

import re
from datetime import datetime, timezone
from pathlib import Path


MEMORY_DIR = Path(__file__).parent

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "in", "of", "to", "and", "or", "for",
    "not", "with", "on", "at", "from", "by", "no", "be", "are",
    "was", "it", "this", "that", "which", "when", "if", "as",
    "has", "have", "had", "does", "did", "do", "its", "but", "so",
})


class MemoryManager:

    def __init__(self, memory_dir: str | Path = MEMORY_DIR) -> None:
        self.root = Path(memory_dir)
        self.root.mkdir(exist_ok=True)
        (self.root / "patterns").mkdir(exist_ok=True)
        (self.root / "runs").mkdir(exist_ok=True)

    # ── Read ─────────────────────────────────────────────────────────────────

    def load_context(self) -> str:
        """
        Load the full memory context for injection into the Orchestrator.
        Returns a markdown string summarising all persistent knowledge.
        """
        sections: list[str] = ["# Project memory\n"]

        overview = self.root / "project-overview.md"
        if overview.exists():
            sections += ["## Project overview", overview.read_text(), ""]

        adr = self.root / "architecture-decisions.md"
        if adr.exists():
            sections += ["## Architecture decisions", adr.read_text()[-2000:], ""]

        patterns_dir = self.root / "patterns"
        pattern_files = [f for f in patterns_dir.glob("*.md") if f.is_file()]
        if pattern_files:
            sections.append("## Established patterns")
            for pf in pattern_files:
                first_line = pf.read_text().splitlines()[0].lstrip("#").strip()
                sections.append(f"- [[{pf.stem}]]: {first_line}")
            sections.append("")

        runs_dir = self.root / "runs"
        recent = sorted(runs_dir.glob("*.md"), reverse=True)[:5]
        if recent:
            sections.append("## Recent runs")
            for run in recent:
                lines = run.read_text().splitlines()
                title  = lines[0].lstrip("#").strip() if lines else run.stem
                summary_line = next((l for l in lines[1:] if l.strip()), "")
                sections.append(f"- **{run.stem}**: {title} — {summary_line[:100]}")
            sections.append("")

        return "\n".join(sections)

    # ── Write ────────────────────────────────────────────────────────────────

    def record_run(
        self,
        intent_summary: str,
        design_summary: str,
        build_summary:  str,
        review_verdict: str,
        test_summary:   str,
        qa_verdict:     str,
    ) -> None:
        """
        Write a run log file and update architecture-decisions.md.
        Called by the Orchestrator after a successful pipeline run.
        """
        ts       = datetime.now(timezone.utc)
        date_str = ts.strftime("%Y-%m-%d")
        slug     = intent_summary[:40].lower().replace(" ", "-").replace("/", "-")
        slug     = "".join(c for c in slug if c.isalnum() or c == "-")
        filename = f"{date_str}-{slug}.md"

        review_ok = review_verdict.strip().upper().startswith("APPROVED")
        qa_ok     = qa_verdict.strip().upper().startswith("APPROVED")

        run_content = f"""# {intent_summary}

Date: {ts.isoformat()}

## What was built
{build_summary}

## Design
{design_summary}

## Code review
**Verdict:** {"✅ APPROVED" if review_ok else "⚠️ CHANGES REQUESTED"}
{review_verdict[:300]}

## QA
**Verdict:** {"✅ APPROVED" if qa_ok else "⚠️ COVERAGE INSUFFICIENT"}
{test_summary}

## Links
- [[architecture-decisions]]
- [[api-conventions]]
"""
        (self.root / "runs" / filename).write_text(run_content)
        self._append_decision(intent_summary, design_summary, ts)
        self._update_overview_if_needed()

    def update_pattern(self, pattern_name: str, content: str) -> None:
        """
        Write or update a pattern note in memory/patterns/.
        Called when agents identify a recurring pattern worth recording.
        """
        path = self.root / "patterns" / f"{pattern_name}.md"
        path.write_text(content)

    def init_project_overview(self, content: str) -> None:
        """Write the initial project-overview.md if it doesn't exist."""
        path = self.root / "project-overview.md"
        if not path.exists():
            path.write_text(content)

    # ── Private ──────────────────────────────────────────────────────────────

    def _append_decision(self, intent: str, design: str, ts: datetime) -> None:
        adr_path = self.root / "architecture-decisions.md"
        entry = (
            f"\n## {ts.strftime('%Y-%m-%d')} — {intent[:60]}\n"
            f"{design[:400]}\n"
        )
        if adr_path.exists():
            adr_path.write_text(adr_path.read_text() + entry)
        else:
            adr_path.write_text(f"# Architecture decisions\n\n{entry}")

    def record_rejection_findings(
        self,
        reviewer: str,       # "Code Reviewer" or "QA Engineer"
        target: str,         # "Coder" or "Tester"
        findings: list[str],
        run_date: str,       # "YYYY-MM-DD" — run discriminator for recurrence
    ) -> None:
        """
        Append rejection findings to memory/patterns/rejection-patterns.md.
        Recurrence is detected across distinct run dates using word-overlap.
        Findings seen in ≥ 2 separate runs are promoted to the "Recurring" section
        and injected into agent prompts on future runs.
        """
        if not findings:
            return

        path = self.root / "patterns" / "rejection-patterns.md"
        existing = path.read_text() if path.exists() else ""

        log_entries = self._parse_rejection_log(existing)

        new_block = (
            f"### {run_date} — {reviewer} → {target}\n"
            + "\n".join(f"- {f}" for f in findings)
            + "\n"
        )
        log_entries.append({"date": run_date, "reviewer": reviewer,
                             "target": target, "findings": findings})

        recurring_coder  = self._find_recurring(log_entries, "Coder")
        recurring_tester = self._find_recurring(log_entries, "Tester")

        lines = [
            "# Rejection patterns\n",
            "_Automatically updated after each pipeline rejection._",
            "_Recurring issues (≥ 2 separate runs) are injected into agent prompts._\n",
            "## Recurring (≥ 2 runs) — injected into prompts\n",
        ]
        if recurring_coder:
            lines.append("### Coder ← Code Reviewer")
            for finding, count in recurring_coder:
                lines.append(f"- [{count}×] {finding}")
            lines.append("")
        if recurring_tester:
            lines.append("### Tester ← QA Engineer")
            for finding, count in recurring_tester:
                lines.append(f"- [{count}×] {finding}")
            lines.append("")
        if not recurring_coder and not recurring_tester:
            lines.append("_No recurring issues yet._\n")

        lines.append("## Observation log\n")
        # Prepend the new entry; preserve all older entries below it.
        old_log = ""
        if "## Observation log" in existing:
            old_log = existing.split("## Observation log", 1)[1].strip()
        lines.append(new_block)
        if old_log:
            lines.append(old_log)

        path.write_text("\n".join(lines) + "\n")

    # ── Private ──────────────────────────────────────────────────────────────

    def _parse_rejection_log(self, content: str) -> list[dict]:
        if "## Observation log" not in content:
            return []
        log_section = content.split("## Observation log", 1)[1]
        entries = []
        for block in re.split(r"\n###\s+", log_section):
            lines = block.strip().splitlines()
            if not lines:
                continue
            m = re.match(r"(\d{4}-\d{2}-\d{2})\s+[—-]+\s+(.+?)\s+[→>]+\s+(\w+)", lines[0])
            if not m:
                continue
            date, reviewer, target = m.group(1), m.group(2).strip(), m.group(3).strip()
            findings = [l[2:].strip() for l in lines[1:] if l.startswith("- ")]
            entries.append({"date": date, "reviewer": reviewer,
                             "target": target, "findings": findings})
        return entries

    def _content_words(self, text: str) -> frozenset[str]:
        return frozenset(
            w.lower() for w in re.findall(r"\b\w+\b", text)
            if len(w) > 2 and w.lower() not in _STOP_WORDS
        )

    def _overlaps(self, a: str, b: str, threshold: int = 3) -> bool:
        return len(self._content_words(a) & self._content_words(b)) >= threshold

    def _find_recurring(self, entries: list[dict], target: str) -> list[tuple[str, int]]:
        """Return (canonical_finding, distinct_run_count) for findings in ≥ 2 runs."""
        target_entries = [e for e in entries if e["target"] == target]
        pairs: list[tuple[str, str]] = [
            (f, e["date"])
            for e in target_entries
            for f in e["findings"]
        ]
        if not pairs:
            return []

        clusters: list[dict] = []
        for finding, date in pairs:
            matched = False
            for cluster in clusters:
                if self._overlaps(finding, cluster["canonical"]):
                    cluster["dates"].add(date)
                    matched = True
                    break
            if not matched:
                clusters.append({"canonical": finding, "dates": {date}})

        return sorted(
            [(c["canonical"], len(c["dates"])) for c in clusters if len(c["dates"]) >= 2],
            key=lambda x: -x[1],
        )

    def _update_overview_if_needed(self) -> None:
        path = self.root / "project-overview.md"
        if not path.exists():
            path.write_text(
                "# Project overview\n\n"
                "_This file is auto-generated. Edit to add permanent context "
                "that agents should always be aware of._\n"
            )
