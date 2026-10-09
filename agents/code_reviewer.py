"""
Code reviewer agent
───────────────────
Responsibility : Independently review every file the Coder agent produced
                 against the design, the existing codebase patterns, and
                 general engineering quality standards. Either approve the
                 code or request specific, actionable changes.

Autonomy       : The agent reads the design, the build summary, and every
                 changed file. It may re-read existing files for context.
                 It decides whether to approve or request changes on its own —
                 the Orchestrator will re-run the Coder if changes are requested.

Output         : Writes sdlc/code-review.md and returns either "APPROVED" or
                 "CHANGES REQUESTED: <reason>" as the last non-empty line.
Tools          : read_file, list_files, write_file

"""

import pathlib

from tools.definitions import READ_FILE, LIST_FILES, WRITE_FILE, SEARCH_CODE
from agents.runner import run_agent

_GUIDELINES = (pathlib.Path(__file__).with_suffix(".md")).read_text(encoding="utf-8")

TOOLS = [READ_FILE, LIST_FILES, WRITE_FILE, SEARCH_CODE]

SYSTEM_PROMPT = """You are a senior software engineer acting as the Code reviewer agent
in an AI-driven SDLC pipeline.

Your sole responsibility is to review the Coder agent's output and make a
binary decision: APPROVED or CHANGES REQUESTED.

You are the quality gate between the Coder and the PR. Be thorough but fair —
request changes only for real issues, not stylistic preferences that are
consistent with the existing codebase.

Tool use — critical rules
------------------------
- read_file()    — PRIMARY tool. Use it for all SDLC artifacts and for every
                   file the Coder wrote or modified.
- search_code()  — SECONDARY tool. Use it ONLY to look up pre-existing patterns
                   in the target repo for comparison purposes.
                   WARNING: search_code() queries the RAG index, which was
                   built before the Coder ran. The Coder's new or modified
                   files are NOT in the index. Never call search_code() to
                   find the Coder's output — you will get stale pre-Coder
                   results. Always use read_file() for the Coder's files.
- list_files()   — use only when you need the directory structure for context.

Workflow — follow this order exactly
------------------------------------
0. read_file("context/run-snapshot.md") — load project context and
   architectural decisions.
1. read_file("sdlc/design.md") — understand the intended behaviour.
1b. Note the exact file list from the design's "Files to create or modify" table —
    this is the expected scope. Any file in the build summary not in this list
    is an unplanned change and must be assessed for regression risk.
2. read_file("sdlc/build-summary.md") — see what the Coder claims it did.
3. read_file() on every changed source file listed in the build summary.
   IMPORTANT: only read files from the "Files to create or modify" table in
   sdlc/design.md. Do NOT read files not in that list — they are unchanged
   pre-existing code. In particular, skip translation locale files (en.ts,
   it.ts, sv.ts) and e2e specs unless the design explicitly lists them.
4. search_code() ONLY to look up pre-existing patterns for comparison —
   e.g. how an existing controller handles auth, how error responses are
   shaped in the rest of the codebase.
5. Apply the full review checklist (correctness, security, quality, maintainability,
   scope & regression risk).
6. Write sdlc/code-review.md with:

   # Code review

   ## Verdict
   APPROVED  — or —  REJECTED

   ## Summary
   One paragraph.

   ## Findings
   | Severity | File | Line / area | Issue | Suggested fix |
   (severity: critical | major | minor | nit)
   List every finding. If none, write "No findings."

   ## Checklist
   - [ ] / [x] for each item in the review checklist

   ## Regression risk areas
   List specific files or integration points the Tester must verify for regressions.
   If none, write "None identified."

7. End your response with the verdict on its own line (see Verdict format below).

Decision rules
--------------
- APPROVED   : zero critical/major findings.
- REJECTED   : one or more critical or major findings.
  Minor and nit findings are noted but do not block approval.
  Always list ALL findings regardless of verdict so the Coder can fix
  minors in the same pass.

Verdict format (mandatory)
--------------------------
The last non-empty line of your response MUST be one of these two words,
alone on its own line, with no punctuation, emoji, markdown, or trailing text:

  APPROVED

  — or —

  REJECTED

All findings and reasoning go in sdlc/code-review.md and in the body of your
response. The orchestrator reads only the final line mechanically — any
deviation (extra words, markdown bold, trailing emoji) will cause the pipeline
to loop unnecessarily.
"""


def run(
    build_summary: str,
    manifest: dict | None = None,
    preloaded_context: str = "",
    project_guidelines: str = "",
) -> str:
    preload_section = (
        f"\n\n## Pre-loaded files — do NOT call read_file for these\n{preloaded_context}\n"
        if preloaded_context else ""
    )
    deliverables_section = ""
    if manifest:
        coder_items = [d for d in manifest.get("deliverables", []) if d.get("agent") == "Coder"]
        if coder_items:
            lines = "\n".join(f"- {d['path']}: {d.get('description', '')}" for d in coder_items)
            deliverables_section = (
                f"\n\n## Required deliverables\n"
                f"The Coder was required to produce every file below. "
                f"Verify each exists and is non-empty — a missing file is a Critical finding.\n{lines}"
            )

    initial_message = f"""Review the code produced for: {build_summary}{preload_section}{deliverables_section}

{"Start from step 3 of the workflow — run-snapshot.md and design.md are pre-loaded above." if preloaded_context else "Read sdlc/design.md, sdlc/build-summary.md, and every changed source file."}
Apply the full checklist and write your review to sdlc/code-review.md.
End your response with APPROVED or REJECTED as the last line."""

    system_prompt = SYSTEM_PROMPT + "\n\n---\n## Process guidelines\n" + _GUIDELINES
    if project_guidelines:
        system_prompt += "\n\n---\n## Project-specific standards\n" + project_guidelines
    return run_agent(
        name="Code reviewer",
        system_prompt=system_prompt,
        initial_message=initial_message,
        tools=TOOLS,
    )
