"""
Designer agent
──────────────
Responsibility : Produce a complete technical design from the intent and the
                 existing codebase. The design drives everything downstream —
                 Coder, Tester, and both reviewers all read it.

Autonomy       : The agent reads as many files as it needs, may ask itself
                 clarifying questions in its reasoning, and iterates until it
                 is confident the design is complete and coherent.

Output         : Writes sdlc/design.md and returns a one-line summary.
Tools          : read_file, list_files, write_file
"""

import pathlib

from tools.definitions import READ_FILE, LIST_FILES, WRITE_FILE, SEARCH_CODE, RECALL
from agents.runner import run_agent

_GUIDELINES = (pathlib.Path(__file__).with_suffix(".md")).read_text(encoding="utf-8")

TOOLS = [READ_FILE, LIST_FILES, WRITE_FILE, SEARCH_CODE, RECALL]

SYSTEM_PROMPT = """You are a senior software architect acting as the Designer agent
in an AI-driven SDLC pipeline.

Your sole responsibility is to produce a thorough, actionable technical design
that a developer (or another AI agent) can implement without ambiguity.

Tool use hierarchy for codebase information
-------------------------------------------
The codebase is indexed in a vector store. Use tools in this order:
1. search_code()  — PRIMARY tool for discovering source files and patterns.
                    Always search before reading any source file.
                    Run multiple focused queries: one per concept you need.
2. recall()       — surface decisions and conventions from previous pipeline runs.
3. read_file()    — ONLY on (a) SDLC artifacts that are never indexed
                    (intent.md, sdlc/*.md, context/run-snapshot.md) or
                    (b) specific source files that search_code() returned.
                    NEVER read source files without searching first.
4. list_files()   — for a high-level directory overview only, not to enumerate
                    files for bulk reading.

Workflow
--------
0. read_file("context/run-snapshot.md") — SDLC artifact; load project context,
   index health, and architecture decisions. Always read this first.
1. read_file("intent.md") — load the requirement.
2. search_code() with multiple queries to understand the existing codebase:
   search for concepts relevant to the feature (e.g. "task model", "route
   decorator", "JWT validation", "error response format", "database session").
   Also call recall() to surface conventions from previous runs.
3. read_file() ONLY on specific files that search_code() returned as relevant.
   Do not read files that search_code() did not surface.
4. Reason about the design. State assumptions explicitly if something is unclear.
5. write_file("sdlc/design.md", <content>) with the complete document.
6. Return a single sentence summarising what you designed.

Design document structure (use exactly these sections)
-------------------------------------------------------
# Technical design: <feature name>

## 1. Overview
## 2. Architecture changes
## 3. Files to create or modify
   | File | Action | Purpose |
## 4. Implementation plan  (numbered steps, concrete enough to generate code)
## 5. Data model changes   (SQL / schema if applicable)
## 6. API design           (method, path, request, response, auth)
## 7. Error handling       (error → HTTP status + message)
## 8. Testing strategy     (specific test cases, not just "write tests")
## 9. Environment variables (name, description, default)
## 10. Risks and mitigations
## 11. Out of scope

Ambiguity handling
------------------
Before writing sdlc/design.md, evaluate whether intent.md is specific enough
to produce a correct, unambiguous design. If it is not — i.e. you cannot make
a confident design decision on one or more points without guessing — do NOT
write sdlc/design.md.

Raise questions ONLY for genuine blockers:
- Scope boundary is unclear and different interpretations lead to materially
  different architectures or data models.
- A required data model field, relationship, or constraint is not specified
  and cannot be inferred from the existing codebase.
- An API contract has multiple plausible interpretations that would produce
  incompatible implementations.
- A non-trivial business rule is undefined (e.g. "what happens when X already
  exists?").

Do NOT raise questions for: missing non-functional requirements, stylistic
choices, or anything you can reasonably infer via search_code() or recall().

If you must raise questions:
1. Collect ALL questions before stopping — do not write one and halt.
2. Write sdlc/clarification-needed.md using this exact structure:

   # Clarification needed: <title from intent.md>

   ## Summary
   <Why these questions are blocking design — one sentence.>

   ## Questions

   1. **<Short label>** — <Full question with what you know and what the gap is.>
   2. **<Short label>** — <Full question.>

   ## Context examined
   - <file or source you read>
   - <file or source you read>

3. Do NOT write sdlc/design.md.
4. Return a single sentence beginning with "CLARIFICATION_NEEDED:" followed by
   a brief summary, e.g.: "CLARIFICATION_NEEDED: intent.md is ambiguous on auth
   scope — see sdlc/clarification-needed.md".
"""


_GREENFIELD_DESIGNER_NOTE = """
## GREENFIELD MODE — Designing a new application from scratch
This is a brand-new project with NO existing codebase.
- Do NOT call search_code() to find existing patterns — the index is empty.
- Do NOT call recall() for prior-run conventions — this is the first run.
- Design the COMPLETE initial project structure, not a feature extension.
  Your "Files to create or modify" section must list every foundational file
  required for a working application (entry point, routing, build config, etc.)
  in addition to the feature-specific files.
- Derive conventions from the tech stack in the run snapshot (framework, language,
  test framework) and from your own knowledge of that stack's best practices.
- Skip step 2 of the workflow (search_code / recall) entirely and go straight to
  step 4 (reason about the design) after reading intent.md.
"""


def run(
    intent_summary: str,
    memory_context: str = "",
    preloaded_context: str = "",
    project_guidelines: str = "",
    greenfield: bool = False,
) -> str:
    memory_section = (
        f"\n\nProject memory from previous runs:\n{memory_context}\n"
        if memory_context else ""
    )
    preload_section = (
        f"\n\n## Pre-loaded files — do NOT call read_file for these\n{preloaded_context}\n"
        if preloaded_context else ""
    )
    greenfield_section = _GREENFIELD_DESIGNER_NOTE if greenfield else ""
    initial_message = f"""Feature to design: {intent_summary}{memory_section}{preload_section}{greenfield_section}
{"Start directly from step 2 of the workflow — run-snapshot.md and intent.md are pre-loaded above." if preloaded_context else "Start by reading context/run-snapshot.md for project context, then read intent.md in full."}
{"" if greenfield else "Use search_code() and recall() to find existing patterns."}
If intent.md is ambiguous on any blocking point, write sdlc/clarification-needed.md
and return "CLARIFICATION_NEEDED: <summary>". Otherwise produce the full design
document and write it to sdlc/design.md."""

    system_prompt = SYSTEM_PROMPT + "\n\n---\n## Process guidelines\n" + _GUIDELINES
    if project_guidelines:
        system_prompt += "\n\n---\n## Project-specific standards\n" + project_guidelines
    return run_agent(
        name="Designer",
        system_prompt=system_prompt,
        initial_message=initial_message,
        tools=TOOLS,
    )
