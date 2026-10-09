#!/usr/bin/env python3
"""
Orchestrator — entry point for the agentic SDLC pipeline.

Startup sequence (before any agent runs)
─────────────────────────────────────────
1. Read config from AGENTS.md
2. Load project memory  → inject into agent contexts
3. Run/update RAG index → from configured remote sources
4. Build run snapshot   → shared context file all agents read first
5. Register retriever   → search_code() and recall() tools become live

Then spawn agents in order:
  Designer → Coder ⇄ Code reviewer → Tester ⇄ QA engineer → PR

After successful run:
  Record run in memory → append to architecture-decisions.md
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from agents import designer, coder, code_reviewer, tester, qa_engineer
from agents.runner import get_token_report
from context.connectors import connector_from_config
from context.embedders import embedder_from_config
from context.stores import store_from_config, VectorStore
from context.indexer import Indexer, STATE_FILE as INDEX_STATE_FILE
from context.retriever import Retriever
from context.snapshot import build_snapshot
from memory.manager import MemoryManager
from tools import definitions as tool_defs

REPO_ROOT = Path(__file__).parent
APP_DIR   = REPO_ROOT / "app"

MAX_CODER_RETRIES  = 2
MAX_TESTER_RETRIES = 2
MAX_IMPL_RETRIES   = 2   # outer correctness loop: Coder → Code reviewer → Tester

# ── Checkpoint helpers ────────────────────────────────────────────────────────
# Checkpointing lets a failed pipeline resume from the last successful stage
# rather than re-running everything from scratch.
#
# File: sdlc/checkpoint.json
# Stages: "designer" | "code_review" | "qa"
# Invalidation: the intent hash changes whenever intent.md is edited, which
#   automatically discards any checkpoint from a previous feature request.

def _checkpoint_file(sdlc: Path) -> Path:
    """
    Return the checkpoint path, scoped to the current git branch so that
    concurrent feature branches never share or clobber each other's state.

    Derivation order:
      1. git rev-parse --abbrev-ref HEAD — primary; the workflow always checks
         out the feature branch before the orchestrator runs, so HEAD is the
         correct branch in both the issue-label and PR-merge trigger paths.
      2. PIPELINE_BRANCH env var — fallback for local runs where HEAD may not
         be on the feature branch.
      3. "default" — hard fallback (detached HEAD / bare checkout edge case).

    GITHUB_REF_NAME is intentionally NOT used: for pull_request events GitHub
    Actions sets it to the PR merge ref (e.g. "42/merge") rather than the branch
    name, which produces a different checkpoint slug on every resume run.

    Branch names are sanitised to safe filename characters so that names
    like "feature/add-login" produce "checkpoint-feature-add-login.json"
    rather than a path with a directory separator.
    """
    branch = ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        if result.returncode == 0 and result.stdout.strip() not in ("HEAD", ""):
            branch = result.stdout.strip()
    except Exception:
        pass
    if not branch:
        branch = os.environ.get("PIPELINE_BRANCH", "").strip()
    slug = re.sub(r"[^a-zA-Z0-9._-]", "-", branch or "default")
    return sdlc / f"checkpoint-{slug}.json"


def _intent_hash(intent: str) -> str:
    """Stable 16-hex-char fingerprint of the current intent.md content."""
    return hashlib.sha256(intent.encode()).hexdigest()[:16]


def _load_checkpoint(sdlc: Path, intent: str) -> dict:
    """
    Return the stored checkpoint if it matches the current intent, else {}.

    A non-matching hash means intent.md changed — the pipeline must restart
    from scratch so the new intent is processed with a clean slate.
    """
    path = _checkpoint_file(sdlc)
    print(f"  Checkpoint path: {path}")
    if not path.exists():
        print(f"  Checkpoint not found — fresh run")
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        print(f"  Checkpoint unreadable ({e}) — fresh run")
        return {}
    saved_hash = data.get("intent_hash", "")
    current_hash = _intent_hash(intent)
    if saved_hash != current_hash:
        print(f"  Intent hash mismatch (saved={saved_hash}, current={current_hash}) — discarding checkpoint")
        return {}
    print(f"  Checkpoint loaded: completed={data.get('completed', [])}")
    return data


def _mark_stage_done(sdlc: Path, intent: str, stage: str, extras: dict) -> None:
    """
    Append *stage* to the checkpoint's completed list and persist *extras*
    (the summary strings the orchestrator passes between stages).

    Safe to call multiple times for the same stage — idempotent.
    """
    path = _checkpoint_file(sdlc)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            pass
    ih = _intent_hash(intent)
    if data.get("intent_hash") != ih:
        data = {"intent_hash": ih, "completed": []}
    if stage not in data["completed"]:
        data["completed"].append(stage)
    data.update(extras)
    issue_number = os.environ.get("ISSUE_NUMBER", "")
    if issue_number:
        data["issue_number"] = issue_number
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, indent=2))


def _clear_checkpoint(sdlc: Path) -> None:
    """Remove the checkpoint file after a fully successful pipeline run."""
    _checkpoint_file(sdlc).unlink(missing_ok=True)


def main() -> None:
    # Generate a unique run ID and share it across all agents via env var
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")
    os.environ["PIPELINE_RUN_ID"] = run_id
    print(f"  Run ID: {run_id}")
    
    print("\n" + "═" * 60)
    print("  🤖  AI-SDLC Agentic Pipeline  (with RAG + Memory)")
    print("═" * 60 + "\n")

    intent_path = REPO_ROOT / "intent.md"
    if not intent_path.exists():
        sys.exit("ERROR: intent.md not found")

    intent  = intent_path.read_text()
    config  = _read_config()
    _prompts = config.get("pipeline", {}).get("prompts", {})

    sdlc    = REPO_ROOT / "sdlc"
    sdlc.mkdir(exist_ok=True)

    # Load checkpoint before touching any artifacts — a resume must keep the
    # artifacts that already-completed stages wrote.
    ckpt      = _load_checkpoint(sdlc, intent)
    completed = set(ckpt.get("completed", []))
    if completed:
        _banner("Resuming from checkpoint")
        print(f"  Completed stages: {', '.join(sorted(completed))}")

    # Clear stale artifacts only on a fresh run (not resuming).
    # On resume, all artifacts must stay so downstream agents can read them.
    if not completed:
        for _f in sdlc.glob("*.md"):
            _f.unlink(missing_ok=True)
        for _f in sdlc.glob("*.json"):
            _f.unlink(missing_ok=True)
        for _f in sdlc.glob("*.txt"):
            _f.unlink(missing_ok=True)
        # Persist intent so the dashboard can display it per run.
        (sdlc / "intent.md").write_text(intent)

    # ── 1. Load project memory ────────────────────────────────────────────────
    _banner("Loading project memory")
    memory = MemoryManager(REPO_ROOT / "memory")
    _init_project_overview(memory, config)
    memory_context = memory.load_context()
    print(f"  Memory loaded ({len(memory_context)} chars)")

    # ── 1b. Sync target application repository ────────────────────────────────
    _banner("Syncing target repository → ./app")
    _sync_app_repo(config)

    # ── 1b2. Capture baseline test failures (fresh runs only) ─────────────────
    # Run the full suite on the virgin clone before the Coder writes anything.
    # The output file (sdlc/test-baseline.txt) lets the Tester classify failures
    # as PRE-EXISTING rather than REGRESSION, preventing false rejections.
    if not completed:
        _banner("Capturing pre-existing test failures (baseline)")
        _capture_test_baseline(sdlc)

    # ── 1c. Restore persisted test suite ─────────────────────────────────────
    _banner("Restoring test suite")
    suite_count = _restore_test_suite()

    # ── 2. Initialise RAG index ───────────────────────────────────────────────
    _banner("Initialising RAG index")
    retriever, store = _init_rag(config)

    # ── 3. Build run snapshot ─────────────────────────────────────────────────
    _banner("Building run snapshot")
    build_snapshot(
        store=store,
        memory_dir=str(REPO_ROOT / "memory"),
        intent=intent,
        config=config,
    )
    print(f"  Snapshot written to context/run-snapshot.md")

    # ── 4. Register retriever in tools ────────────────────────────────────────
    tool_defs.set_retriever(retriever)

    # ── Pre-load shared SDLC artifacts ────────────────────────────────────────
    # Read once here and inject into each agent's initial message so agents
    # skip the read_file tool calls for these files. This eliminates the largest
    # early-turn tool results from the conversation history, reducing token growth.
    snapshot_path = REPO_ROOT / "context" / "run-snapshot.md"
    snapshot_text = snapshot_path.read_text() if snapshot_path.exists() else ""

    designer_preload = (
        f"### context/run-snapshot.md\n{snapshot_text}\n\n"
        f"### intent.md\n{intent}\n"
    )

    # ── Stage 1: Designer ─────────────────────────────────────────────────────
    _banner("Stage 1 · Designer")
    _ran_designer = "designer" not in completed
    if "designer" in completed:
        design_summary = ckpt["design_summary"]
        print(f"  ↩️  Skipping — already completed in previous run")
        print(f"  Design: {design_summary}")
    else:
        design_summary = designer.run(
            intent_summary=_first_line(intent),
            memory_context=memory_context,
            preloaded_context=designer_preload,
            project_guidelines=_prompts.get("design_guidelines", ""),
        )

        clarification_path = sdlc / "clarification-needed.md"
        if clarification_path.exists():
            # Do NOT checkpoint — the intent needs to be fixed before proceeding.
            print("\n❓ Designer has questions — opening clarification PR and halting pipeline.")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                _banner("Creating clarification PR")
                _create_clarification_pr(_first_line(intent))
            else:
                print(f"  (local run — see {clarification_path} for questions)")
            return

        _mark_stage_done(sdlc, intent, "designer", {"design_summary": design_summary})
        print(f"\n✅ Design: {design_summary}\n")

    # Gate fires only when the designer ran in THIS execution — not on resume runs
    # where the stage was already reviewed and approved via a merged PR.
    if _ran_designer and _gate_enabled(config, "after_design"):
        _banner("Human gate: after design")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            _create_gate_pr(intent, "after_design")
            return
        print("  (local run — continuing past gate)")

    # Design is now written — read it once for all downstream agents
    design_path = sdlc / "design.md"
    design_text = design_path.read_text() if design_path.exists() else ""
    downstream_preload = (
        f"### context/run-snapshot.md\n{snapshot_text}\n\n"
        f"### sdlc/design.md\n{design_text}\n"
    )

    # ── Stage 2+3a: Correctness loop — Coder → Code reviewer → Tester ──────────
    # The outer loop runs until the Tester reports no implementation bugs (or
    # MAX_IMPL_RETRIES is exhausted). The Coder is re-invoked with the Tester's
    # bug report so failures drive fixes at the source rather than looping the
    # Tester against code it cannot change.
    _banner("Stage 2 · Coder → Code reviewer → Tester (correctness loop)")
    # Load recurring rejection patterns once; inject into every Coder / Tester run.
    _coder_guidelines  = _prompts.get("build_guidelines", "") + _recurring_patterns_note(memory, "Coder")
    _tester_guidelines = _prompts.get("test_guidelines", "") + _recurring_patterns_note(memory, "Tester")
    manifest = None   # populated inside else-block; declared here for Stage 3b scope
    _ran_code_review = "code_review" not in completed
    if "code_review" in completed:
        build_summary  = ckpt["build_summary"]
        review_verdict = ckpt["review_verdict"]
        test_summary   = ckpt.get("test_summary_pre_qa", "")
        print(f"  ↩️  Skipping — already completed in previous run")
    else:
        build_summary = review_verdict = test_summary = None
        manifest    = load_manifest(workspace=str(sdlc)) if (sdlc / "deliverables.json").exists() else None
        coder_extra = build_coder_context(manifest) if manifest else ""

        for impl_attempt in range(1, MAX_IMPL_RETRIES + 1):
            if impl_attempt > 1:
                print(f"\n  Correctness attempt {impl_attempt}/{MAX_IMPL_RETRIES} — re-running Coder with bug report…")

            # ── Coder + Code reviewer inner loop ──────────────────────────────
            for attempt in range(1, MAX_CODER_RETRIES + 1):
                print(f"\n  Coder attempt {attempt}/{MAX_CODER_RETRIES}")
                build_summary = coder.run(
                    design_summary=design_summary,
                    memory_context=memory_context,
                    preloaded_context=downstream_preload,
                    extra_context=coder_extra,
                    project_guidelines=_coder_guidelines,
                )
                print(f"\n✅ Build: {build_summary}")

                if manifest:
                    missing = check_deliverables(manifest, str(APP_DIR))
                    if missing:
                        print(f"\n  ⚠️  Missing deliverables: {missing}")
                        if attempt < MAX_CODER_RETRIES:
                            feedback = build_missing_feedback(missing, manifest)
                            coder_extra = (
                                build_coder_context(manifest)
                                + f"\n\n## ⚠️ Previous Attempt — Missing Files\n{feedback}"
                            )
                        else:
                            _write_unresolved_findings(sdlc, "coder", missing)
                    else:
                        print(f"\n  ✅ All deliverables present")
                        coder_extra = build_coder_context(manifest)

                review_verdict = code_reviewer.run(
                    build_summary=build_summary,
                    manifest=manifest,
                    preloaded_context=downstream_preload,
                    project_guidelines=_prompts.get("code_review_guidelines", ""),
                )
                print(f"\n  Review: {review_verdict}")
                if not _is_approved(review_verdict):
                    _record_rejection(memory, sdlc / "code-review.md", "Code Reviewer", "Coder")

                if _is_approved(review_verdict):
                    print("✅ Code review: APPROVED")
                    break
                if attempt == MAX_CODER_RETRIES:
                    print(f"⚠️  Proceeding after {MAX_CODER_RETRIES} coder attempts (see sdlc/code-review.md)")
                    break
                print(f"  Changes requested — re-running Coder…")
                review_feedback = (
                    f"## ⚠️ Code Review — Changes Required (attempt {attempt})\n"
                    f"Your previous implementation was rejected by the Code reviewer.\n"
                    f"Verdict: {review_verdict}\n\n"
                    f"Read sdlc/code-review.md for the full findings table. You MUST address "
                    f"every critical and major finding before resubmitting. Do not re-introduce "
                    f"the same issues."
                )
                coder_extra = (build_coder_context(manifest) if manifest else "") + f"\n\n{review_feedback}"

            # ── Tester (correctness check — no QA yet) ────────────────────────
            tester_extra = build_tester_context(manifest) if manifest else ""
            print(f"\n  Running Tester (correctness check)…")
            test_summary = tester.run(
                feature_summary=design_summary,
                existing_suite=suite_count > 0,
                preloaded_context=downstream_preload,
                extra_context=tester_extra,
                project_guidelines=_tester_guidelines,
            )
            print(f"\n✅ Tests: {test_summary}")

            # ── Gate: implementation bugs → back to Coder ─────────────────────
            # Use the report file as authoritative source: the model sometimes
            # returns REJECTED even when its own report has no bugs/regressions.
            has_bugs = _tester_has_real_bugs(sdlc)
            if not has_bugs:
                if not _is_approved(test_summary):
                    print("  ⚠️  Tester wrote REJECTED but report shows no bugs/regressions — treating as APPROVED")
                print("  No implementation bugs found — proceeding to QA coverage review.")
                break
            if impl_attempt == MAX_IMPL_RETRIES:
                print(f"⚠️  Implementation bugs remain after {MAX_IMPL_RETRIES} correctness attempts — proceeding.")
                break
            print(f"  Implementation bugs found — feeding back to Coder…")
            coder_extra = (build_coder_context(manifest) if manifest else "") + (
                f"\n\n## ⚠️ Implementation bugs found by Tester (correctness attempt {impl_attempt})\n"
                f"{_read_bugs_section(sdlc)}\n\n"
                f"You MUST fix every bug listed above. Do not resubmit code that reproduces "
                f"the same failures. Read sdlc/test-results.md for the full context."
            )

        _mark_stage_done(sdlc, intent, "code_review", {
            "build_summary":       build_summary,
            "review_verdict":      review_verdict,
            "test_summary_pre_qa": test_summary,
        })

    if _ran_code_review and _gate_enabled(config, "after_code_review"):
        _banner("Human gate: after code review")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            _create_gate_pr(intent, "after_code_review")
            return
        print("  (local run — continuing past gate)")

    # ── Stage 3b: QA engineer — coverage loop ─────────────────────────────────
    # Only reached when the implementation is correct (Tester found no bugs).
    # QA reviews coverage quality; if insufficient, only the Tester is retried.
    _banner("Stage 3 · QA engineer (coverage loop)")
    _ran_qa = "qa" not in completed
    if "qa" in completed:
        test_summary = ckpt["test_summary"]
        qa_verdict   = ckpt["qa_verdict"]
        print(f"  ↩️  Skipping — QA already approved in previous run")
    else:
        qa_verdict   = None
        tester_extra = build_tester_context(manifest) if manifest else ""

        for attempt in range(1, MAX_TESTER_RETRIES + 1):
            print(f"\n  QA attempt {attempt}/{MAX_TESTER_RETRIES}")

            if manifest:
                test_missing = [
                    m for m in check_deliverables(manifest, str(sdlc))
                    if any(d["path"] == m and d["agent"] == "Tester"
                           for d in manifest["deliverables"])
                ]
                if test_missing:
                    print(f"\n  ⚠️  Missing test deliverables: {test_missing}")
                    if attempt < MAX_TESTER_RETRIES:
                        feedback = build_missing_feedback(test_missing, manifest)
                        tester_extra = (
                            build_tester_context(manifest)
                            + f"\n\n## ⚠️ Previous Attempt — Missing Files\n{feedback}"
                        )
                    else:
                        _write_unresolved_findings(sdlc, "tester", test_missing)

            qa_verdict = qa_engineer.run(
                test_summary=test_summary,
                existing_suite=suite_count > 0,
                preloaded_context=downstream_preload,
                project_guidelines=_prompts.get("qa_guidelines", ""),
            )
            print(f"\n  QA: {qa_verdict}")
            if not _is_approved(qa_verdict):
                _record_rejection(memory, sdlc / "qa-review.md", "QA Engineer", "Tester")

            if _is_approved(qa_verdict):
                print("✅ QA review: APPROVED")
                break
            if attempt == MAX_TESTER_RETRIES:
                print(f"⚠️  Proceeding after {MAX_TESTER_RETRIES} QA attempts (see sdlc/qa-review.md)")
                break
            print(f"  Coverage insufficient — re-running Tester…")
            test_summary = tester.run(
                feature_summary=design_summary,
                qa_feedback=qa_verdict,
                existing_suite=suite_count > 0,
                preloaded_context=downstream_preload,
                extra_context=tester_extra,
                project_guidelines=_tester_guidelines,
            )
            print(f"\n✅ Tests (coverage retry): {test_summary}")

        _mark_stage_done(sdlc, intent, "qa", {
            "test_summary": test_summary,
            "qa_verdict":   qa_verdict,
        })

    # ── Persist updated test suite ────────────────────────────────────────────
    _banner("Persisting test suite")
    _save_test_suite()

    if _ran_qa and _gate_enabled(config, "after_qa_review"):
        _banner("Human gate: after QA review")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            _create_gate_pr(intent, "after_qa_review")
            return
        print("  (local run — continuing past gate)")

    # ── Record run in memory ──────────────────────────────────────────────────
    _banner("Recording run in memory")
    memory.record_run(
        intent_summary=_first_line(intent),
        design_summary=design_summary or "",
        build_summary=build_summary or "",
        review_verdict=review_verdict or "",
        test_summary=test_summary or "",
        qa_verdict=qa_verdict or "",
    )
    _update_patterns(memory, intent, design_summary or "", build_summary or "")
    print("  Run recorded in memory/runs/")

    # ── Token usage report ────────────────────────────────────────────────────
    _banner("Token usage")
    _write_token_report(sdlc)

    # ── Pipeline summary (PR body) ────────────────────────────────────────────
    _write_pipeline_summary(
        sdlc,
        intent,
        design_summary  or "",
        build_summary   or "",
        review_verdict  or "",
        test_summary    or "",
        qa_verdict      or "",
    )

    # ── Create PR ─────────────────────────────────────────────────────────────
    if os.environ.get("GITHUB_ACTIONS") == "true":
        _banner("Creating PR")
        _create_pr(_first_line(intent))

    # Clear checkpoint — pipeline ran to completion; next invocation starts fresh.
    _clear_checkpoint(sdlc)

    _banner("Pipeline complete")
    print("  context/run-snapshot.md  — run context")
    print("  sdlc/design.md           — technical design")
    print("  sdlc/build-summary.md    — implementation notes")
    print("  sdlc/code-review.md      — code review")
    print("  sdlc/test-results.md     — test results")
    print("  sdlc/qa-review.md        — QA assessment")
    print("  sdlc/token-usage.md      — token consumption")
    print("  memory/runs/             — run log appended\n")


def _capture_test_baseline(sdlc: Path) -> None:
    """Run the full test suite on the virgin app clone and record failures.

    Called once at the start of a fresh run, before the Coder writes any code.
    The resulting sdlc/test-baseline.txt lets the Tester distinguish pre-existing
    failures from true regressions caused by the current feature.
    """
    baseline_file = sdlc / "test-baseline.txt"
    if baseline_file.exists():
        return  # already captured (resume run)
    app_dir = REPO_ROOT / "app"
    if not app_dir.exists():
        return  # app not cloned yet; baseline will be skipped
    print("  Running baseline test suite on virgin app clone…")
    result = subprocess.run(
        ["npx", "ng", "test", "--no-watch", "--no-progress"],
        cwd=app_dir, capture_output=True, text=True, timeout=300,
    )
    combined = result.stdout + result.stderr
    # Strip ANSI escape codes so matching works regardless of terminal colour
    import re as _re
    _ansi = _re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    clean = _ansi.sub("", combined)
    # Capture Vitest FAIL lines (contain "FAIL" and a spec file path)
    # and legacy Karma FAILED lines. Summary lines ("Tests 221 failed") are
    # excluded because they don't contain ".spec.ts".
    failed_lines = sorted({
        ln.strip() for ln in clean.splitlines()
        if ln.strip()
        and ("FAILED" in ln or "FAIL" in ln)
        and (".spec.ts" in ln or ".test.ts" in ln)
    })
    baseline_file.write_text("\n".join(failed_lines))
    print(f"  Baseline captured: {len(failed_lines)} pre-existing failing test(s)")


def _sync_app_repo(config: dict) -> None:
    """
    Populate ./app with the latest content of the configured target_repo.

    The target repo is a READ-ONLY source. Its files are copied into the
    pipeline repo's ./app directory as ordinary tracked files — no nested
    .git directory, no submodule. Agents then modify ./app freely, and
    those changes are committed to the PIPELINE repo via the PR step.
    The target repo itself is never written to or pushed to in any way.

    Sync behaviour
    ──────────────
    The clone is SKIPPED when ./app already contains files, because those
    files are agent-modified code from a previous run. Overwriting them
    with a fresh clone would discard the Coder's work.

    Set SYNC_TARGET_REPO=true to force a fresh clone regardless — use this
    when you explicitly want to pull upstream changes into ./app (e.g. after
    a long break or a major upstream release). Be aware this will reset any
    agent modifications that have not yet been merged back to the target repo.

    Only files whose content actually changed are written to disk, so
    the local connector's mtime-based incremental indexing only re-indexes
    files that differ from the previous pipeline run.
    """
    target = config.get("target_repo")
    if not target:
        print("  No target_repo configured — ./app is managed as part of this repo")
        return

    repo   = target.get("repo", "")
    branch = target.get("branch", "main")
    path   = REPO_ROOT / target.get("local_path", "./app")

    if not repo:
        print("  ⚠️  target_repo.repo is not set — skipping")
        return

    # Skip the clone when ./app is already populated unless explicitly forced.
    # Agent modifications live in ./app between runs; a fresh clone would
    # overwrite them with the upstream source, discarding the Coder's output.
    force_sync = os.environ.get("SYNC_TARGET_REPO", "").lower() == "true"
    already_populated = path.exists() and any(path.iterdir())
    if already_populated and not force_sync:
        print(f"  ./app already populated — skipping clone (set SYNC_TARGET_REPO=true to force)")
        return

    # TARGET_REPO_TOKEN must be a PAT with read access to the target repo.
    # The built-in GITHUB_TOKEN is scoped to the pipeline repo only and
    # cannot access any other repository — using it here always fails.
    token = os.environ.get("TARGET_REPO_TOKEN", "")
    if not token:
        print("  ⚠️  TARGET_REPO_TOKEN not set — clone will fail for private repos")

    if token:
        # x-access-token is GitHub's documented format for PAT/App token auth
        clone_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    else:
        clone_url = f"https://github.com/{repo}.git"

    print(f"  Fetching {repo}@{branch}")

    # Clone into a temp directory that lives outside the pipeline repo, then
    # mirror the file tree into ./app without the .git metadata. This keeps
    # ./app as a plain directory tracked by the pipeline repo — not a nested
    # git repo — so git add app/ works normally in _create_pr.
    with tempfile.TemporaryDirectory() as tmp:
        _run_git([
            "git", "clone",
            "--branch", branch,
            "--single-branch",
            "--depth", "1",
            clone_url, tmp,
        ])
        _mirror_directory(src=Path(tmp), dst=path)

    print(f"  ✅ {repo}@{branch} synced to {path}")


def _mirror_directory(src: Path, dst: Path) -> None:
    """
    Mirror src into dst, excluding .git metadata.

    Only writes files whose content actually changed so unchanged files
    keep their old mtime and are skipped by the incremental indexer.
    Removes files from dst that no longer exist in src.
    """
    dst.mkdir(parents=True, exist_ok=True)

    src_files = {
        f.relative_to(src)
        for f in src.rglob("*")
        if f.is_file() and ".git" not in f.parts
    }
    dst_files = {
        f.relative_to(dst)
        for f in dst.rglob("*")
        if f.is_file()
    }

    for rel in src_files:
        src_f, dst_f = src / rel, dst / rel
        dst_f.parent.mkdir(parents=True, exist_ok=True)
        if not dst_f.exists() or src_f.read_bytes() != dst_f.read_bytes():
            shutil.copy2(src_f, dst_f)

    for rel in dst_files - src_files:
        (dst / rel).unlink(missing_ok=True)


def _restore_test_suite() -> int:
    """
    Copy test-suite/ → app/tests/ so the Tester starts with the persisted suite.

    Returns the number of test files restored (0 on first run when no suite exists yet).
    The target repo sync may have wiped app/tests/ — this puts the pipeline-owned
    suite back before any agent runs.
    """
    src = REPO_ROOT / "test-suite"
    dst = REPO_ROOT / "app" / "tests"

    if not src.exists() or not any(src.rglob("*.py")):
        print("  No persisted test suite found — Tester will create one from scratch")
        return 0

    _mirror_directory(src=src, dst=dst)
    count = sum(1 for _ in src.rglob("*.py"))
    print(f"  Restored {count} test file(s) from test-suite/ → app/tests/")
    return count


def _save_test_suite() -> None:
    """
    Copy app/tests/ → test-suite/ after the Tester has run.

    test-suite/ is committed to the pipeline repo via PIPELINE_ARTIFACTS so
    the suite accumulates across pipeline runs instead of being regenerated
    from scratch each time.
    """
    src = REPO_ROOT / "app" / "tests"
    dst = REPO_ROOT / "test-suite"

    if not src.exists() or not any(src.rglob("*.py")):
        print("  No test files found in app/tests/ — nothing to persist")
        return

    _mirror_directory(src=src, dst=dst)
    count = sum(1 for _ in src.rglob("*.py"))
    print(f"  Persisted {count} test file(s) from app/tests/ → test-suite/")


def _run_git(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"git command failed: {' '.join(cmd)}\n{result.stderr.strip()}"
        )


def _handle_repo_switch(config: dict, store: VectorStore) -> bool:
    """
    Detect whether target_repo changed since the last pipeline run.

    If it did, delete all index vectors that belong to the previous target
    repo (identified by the chunk IDs stored in the state file) and reset
    the local-source state so the new repo is fully reindexed from scratch.

    Returns True when the caller should force a full reindex.

    Limitations
    ───────────
    Detection relies on the state file (.index/index-state.json).
    If the state file was lost (e.g. CI cache miss) AND the repo was
    switched at the same time, stale vectors cannot be identified by chunk
    ID. In that case, manually delete the Qdrant collection and let the
    pipeline recreate it, or set FORCE_FULL_REINDEX=true and accept that
    a small number of orphaned vectors will remain until the collection is
    reset.
    """
    current_repo = (config.get("target_repo") or {}).get("repo", "")
    state_path   = REPO_ROOT / INDEX_STATE_FILE

    if not state_path.exists():
        return False

    try:
        state = json.loads(state_path.read_text())
    except Exception:
        return False

    prev_repo = state.get("_target_repo", "")

    if not prev_repo or not current_repo or prev_repo == current_repo:
        return False

    print(f"  Target repo changed: {prev_repo!r} → {current_repo!r}")
    print(f"  Purging stale vectors from previous target repo…")

    # Collect every chunk ID that was stored under a local:: source.
    # These are exactly the vectors produced by the previous target repo.
    chunks_to_delete: list[str] = []
    local_keys: list[str] = []
    for key, val in state.items():
        if key.startswith("local::") and isinstance(val, dict):
            for chunk_list in val.get("doc_chunks", {}).values():
                chunks_to_delete.extend(chunk_list)
            local_keys.append(key)

    if chunks_to_delete:
        store.delete(chunks_to_delete)
        print(f"  Purged {len(chunks_to_delete)} stale chunks from index")
    else:
        print(f"  No chunk IDs found in state — index may already be clean")

    # Clear local-source state so the indexer treats the new repo as a
    # fresh source and does a full reindex rather than an incremental diff.
    for key in local_keys:
        del state[key]
    state["_target_repo"] = current_repo
    state_path.write_text(json.dumps(state, indent=2))

    return True  # signal to caller: force full reindex


def _record_target_repo(config: dict) -> None:
    """
    Persist the current target_repo name in the state file so the next
    run can detect if it was changed.
    """
    current_repo = (config.get("target_repo") or {}).get("repo", "")
    if not current_repo:
        return

    state_path = REPO_ROOT / INDEX_STATE_FILE
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            pass

    if state.get("_target_repo") == current_repo:
        return  # already recorded, nothing to write

    state["_target_repo"] = current_repo
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))


def _init_rag(config: dict) -> tuple[Retriever | None, VectorStore | None]:
    """
    Initialise the RAG pipeline from AGENTS.md config.

    Returns (retriever, store). Both are None if RAG is unavailable.
    Returning the store avoids a second store_from_config() call in the
    caller, which would re-connect to Qdrant and could fail if credentials
    are temporarily unavailable — causing a false "Index not available" in
    the run snapshot.

    Connector priority:
      1. target_repo.local_path — the cloned application codebase (auto-added
         when target_repo is configured; never duplicated via sources).
      2. sources[]              — additional reference sources (docs, etc.).
    """
    ctx_cfg = config.get("pipeline", {}).get("context", {})

    target_cfg   = config.get("target_repo")
    sources_cfg  = config.get("sources", [])
    embedder_cfg = ctx_cfg.get("embedder", {"provider": "sentence_transformers"})
    store_cfg    = ctx_cfg.get("store",    {"provider": "chroma"})

    # Build connector list: target repo first, then reference sources.
    connector_cfgs: list[dict] = []

    if target_cfg:
        # Derive a local connector from target_repo so users never have to
        # configure ./app in sources — that would be a confusing duplicate.
        idx = target_cfg.get("index", {})
        connector_cfgs.append({
            "type": "local",
            "path": target_cfg.get("local_path", "./app"),
            **idx,
        })

    connector_cfgs.extend(sources_cfg)

    if not connector_cfgs:
        print("  No sources configured — RAG unavailable. Agents will use direct file reads.")
        return None, None

    try:
        connectors = [connector_from_config(c) for c in connector_cfgs]
        embedder   = embedder_from_config(embedder_cfg)
        store      = store_from_config(store_cfg)

        # Detect a target_repo switch and purge stale vectors before indexing.
        force_full = os.environ.get("FORCE_FULL_REINDEX", "").lower() == "true"
        force_full = _handle_repo_switch(config, store) or force_full

        indexer = Indexer(
            connectors=connectors,
            embedder=embedder,
            store=store,
            memory_dir=str(REPO_ROOT / "memory"),
        )

        summary = indexer.run(force_full=force_full)

        # Record the active target_repo so future runs can detect a switch.
        _record_target_repo(config)

        total_chunks = sum(v.get("indexed", 0) for v in summary.values())
        print(f"  Index ready: {total_chunks} chunks across {len(connectors)} source(s)")

        retriever = Retriever(store=store, embedder=embedder, memory_dir=str(REPO_ROOT / "memory"))
        return retriever, store

    except Exception as e:
        print(f"  ⚠️  RAG init failed: {e} — agents will use direct file reads.")
        return None, None


def _read_config() -> dict:
    # Try both casings — Linux (GitHub Actions) is case-sensitive.
    for _name in ("AGENTS.md", "AGENTS.md"):
        agents_md = REPO_ROOT / _name
        if agents_md.exists():
            break
    else:
        return {}
    content = agents_md.read_text()
    match   = re.search(r"```yaml\n([\s\S]*?)```", content)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}

def _gate_enabled(config: dict, stage: str) -> bool:
    return (
        config.get("pipeline", {})
              .get("stages", {})
              .get(stage, {})
              .get("human_review", False)
    )


def _resolve_feature_branch() -> str:
    """Determine the feature branch to push SDLC artifacts to.

    Priority:
    1. PIPELINE_BRANCH  — set by the workflow's "Fetch intent" step.
    2. Derived from ISSUE_NUMBER via GitHub CLI — issue-triggered runs where
       the workflow didn't pre-create the branch.
    3. GITHUB_HEAD_REF  — pull_request event source branch.
    4. GITHUB_REF_NAME  — only if it is not a protected trunk branch.
    5. Hard fallback: "feature/pipeline-run".
    """
    branch = os.environ.get("PIPELINE_BRANCH", "").strip()
    if branch:
        return branch

    issue_number = os.environ.get("ISSUE_NUMBER", "").strip()
    if issue_number:
        result = subprocess.run(
            f"gh issue view {issue_number} --json title --jq '.title'",
            shell=True, capture_output=True, text=True,
        )
        title = result.stdout.strip()
        if title:
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            return f"feature/{slug}"

    branch = os.environ.get("GITHUB_HEAD_REF", "").strip()
    if branch:
        return branch

    branch = os.environ.get("GITHUB_REF_NAME", "").strip()
    if branch and branch not in ("main", "master", "develop"):
        return branch

    return "feature/pipeline-run"


def _create_pr(summary: str) -> None:
    """
    Commit SDLC artifacts to the triggering branch and open a PR to main.

    Agent changes are pushed back to the branch that triggered the pipeline run
    rather than a new branch. If a PR from this branch to main already exists,
    the push updates it in place; the gh pr create call is skipped gracefully.
    """
    PIPELINE_ARTIFACTS = [
        "app/",
        "sdlc/",
        "test-suite/",
        "memory/runs/",
        "memory/architecture-decisions.md",
        "memory/project-overview.md",
        "memory/patterns/api-conventions.md",
        "memory/patterns/error-handling.md",
        "memory/patterns/rejection-patterns.md",
        "intent.md",
        "context/run-snapshot.md",
    ]

    # On first runs some artifact paths may not exist yet — only stage what's there.
    existing_artifacts = [
        p for p in PIPELINE_ARTIFACTS
        if (REPO_ROOT / p.rstrip("/")).exists()
    ]
    if not existing_artifacts:
        print("  ⚠️  No artifacts found to commit — skipping PR creation")
        return

    branch = _resolve_feature_branch()

    # Derive a readable PR title from the branch name (strip common prefixes,
    # replace separators with spaces, title-case). Fall back to summary.
    def _branch_title(b: str) -> str:
        stem = re.sub(r"^(feature|feat|fix|chore|refactor|docs?)/", "", b, flags=re.IGNORECASE)
        return re.sub(r"[-_]+", " ", stem).strip().title()

    pr_title = (_branch_title(branch) or summary)[:72]

    cmds = [
        'git config user.name "Claude AI 🤖"',
        'git config user.email "claude-sdlc@anthropic.com"',
        # Create the branch if it doesn't exist; switch to it if it does.
        f"git checkout -b {branch} 2>/dev/null || git checkout {branch}",
        f"git add -- {' '.join(existing_artifacts)}",
        f'git commit -m "🤖 [AI] {pr_title[:72]}"',
        # --force-with-lease is safe: fails only if someone else pushed to
        # the remote branch after our last fetch, which won't happen for
        # pipeline-owned branches.
        f"git push --force-with-lease origin HEAD:{branch}",
        # Ensure the label exists before referencing it (idempotent)
        'gh label create "ai-generated" --color "0075ca" --description "Generated by AI pipeline" --force',
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.stdout.strip():
            print(f"  {result.stdout.strip()}")
        if result.returncode != 0 and result.stderr.strip():
            print(f"  ⚠️  {result.stderr.strip()}")

    # Create PR or update the gate draft PR if one already exists
    pr_check = subprocess.run(
        f"gh pr view {branch} --json state --jq '.state' 2>/dev/null",
        shell=True, capture_output=True, text=True,
    )
    pr_exists = pr_check.returncode == 0 and pr_check.stdout.strip()
    if pr_exists:
        for cmd in [
            f'gh pr edit {branch} --title "🤖 [AI] {pr_title}" --body-file sdlc/pipeline-summary.md',
            f'gh pr ready {branch}',
        ]:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.stdout.strip():
                print(f"  {result.stdout.strip()}")
            if result.returncode != 0 and result.stderr.strip():
                print(f"  ⚠️  {result.stderr.strip()}")
    else:
        result = subprocess.run(
            f'gh pr create --title "🤖 [AI] {pr_title}" --body-file sdlc/pipeline-summary.md --base main --head {branch} --label ai-generated',
            shell=True, capture_output=True, text=True,
        )
        if result.stdout.strip():
            print(f"  {result.stdout.strip()}")
        if result.returncode != 0 and result.stderr.strip():
            print(f"  ⚠️  {result.stderr.strip()}")

    issue_number = os.environ.get("ISSUE_NUMBER", "")
    if issue_number:
        pr_url_result = subprocess.run(
            f"gh pr view {branch} --json url --jq '.url'",
            shell=True, capture_output=True, text=True
        )
        pr_url = pr_url_result.stdout.strip()
        subprocess.run(
            f'gh issue comment {issue_number} --body '
            f'"✅ Pipeline complete — PR ready for review: {pr_url}"',
            shell=True
        )
        subprocess.run(
            f'gh issue edit {issue_number} --remove-label "in-build" --add-label "in-review"',
            shell=True
        )


def _build_clarification_pr_body(intent_title: str, questions_content: str) -> str:
    return f"""## 🤖 AI Designer — Clarification needed

The AI pipeline started processing **{intent_title}** but could not produce
a design because `intent.md` contains requirements that are ambiguous or
incomplete.

Please answer the questions below by editing `intent.md`, then push to
re-trigger the pipeline.

---

{questions_content}

---

### Next steps

1. Read the questions above.
2. Edit `intent.md` on the branch that triggered this run to answer them.
3. Push — the pipeline will re-run and the Designer will proceed.

> This PR was created automatically by the AI-SDLC pipeline.
> No application code changes have been made.
"""


def _create_clarification_pr(intent_title: str) -> None:
    """
    Open a PR with the Designer's clarification questions as the PR body.

    Targets the branch that triggered the current pipeline run:
      GITHUB_HEAD_REF  — PR-triggered runs (the source branch)
      GITHUB_REF_NAME  — push-triggered runs (the pushed branch)
      "main"           — hard fallback
    """
    base_branch = (
        os.environ.get("GITHUB_HEAD_REF", "").strip()
        or os.environ.get("GITHUB_REF_NAME", "").strip()
        or "main"
    )

    clarification_path = REPO_ROOT / "sdlc" / "clarification-needed.md"
    if not clarification_path.exists():
        print("  ⚠️  sdlc/clarification-needed.md not found — cannot create PR")
        return

    pr_body = _build_clarification_pr_body(intent_title, clarification_path.read_text())
    pr_body_path = REPO_ROOT / "sdlc" / "clarification-pr-body.md"
    pr_body_path.write_text(pr_body)

    branch = f"ai/clarify-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
    title  = f"🤖 [AI] Clarification needed — {intent_title[:55]}"

    cmds = [
        'git config user.name "Claude AI 🤖"',
        'git config user.email "claude-sdlc@anthropic.com"',
        f"git checkout -b {branch}",
        "git add -- sdlc/clarification-needed.md sdlc/clarification-pr-body.md context/run-snapshot.md",
        f'git commit -m "🤖 [AI] Clarification needed: {intent_title[:65]}"',
        f"git push origin {branch}",
        'gh label create "ai-clarification" --color "e4e669" --description "AI pipeline needs clarification before proceeding" --force',
        f'gh pr create --title "{title}" --body-file sdlc/clarification-pr-body.md --base {base_branch} --label ai-clarification',
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.stdout.strip():
            print(f"  {result.stdout.strip()}")
        if result.returncode != 0 and result.stderr.strip():
            print(f"  ⚠️  {result.stderr.strip()}")

    issue_number = os.environ.get("ISSUE_NUMBER", "")
    if issue_number:
        subprocess.run(
            f'gh issue comment {issue_number} --body-file sdlc/clarification-needed.md',
            shell=True
        )
        subprocess.run(
            f'gh issue edit {issue_number} '
            f'--remove-label "in-build" --add-label "needs-clarification"',
            shell=True
        )


def _create_gate_pr(intent: str, stage: str) -> None:
    """
    Commit SDLC artifacts to a review/* branch, open a PR targeting the feature
    branch (not main), and pause the pipeline.  main is never touched until the
    full pipeline completes.

    Resume path: merge the PR into the feature branch — the pull_request.closed
    workflow trigger re-runs the orchestrator, which reloads the checkpoint and
    skips already-completed stages.
    """
    GATE_ARTIFACTS = [
        "app/",
        "sdlc/",
        "test-suite/",
        "memory/runs/",
        "memory/architecture-decisions.md",
        "memory/project-overview.md",
        "memory/patterns/api-conventions.md",
        "memory/patterns/error-handling.md",
        "memory/patterns/rejection-patterns.md",
        "intent.md",
        "context/run-snapshot.md",
    ]
    existing = [p for p in GATE_ARTIFACTS if (REPO_ROOT / p.rstrip("/")).exists()]
    if not existing:
        print("  ⚠️  No artifacts found — skipping gate PR creation")
        return

    branch = _resolve_feature_branch()
    title  = _first_line(intent)
    stage_label = {
        "after_design":      "design",
        "after_code_review": "code review",
        "after_qa_review":   "QA review",
    }.get(stage, stage)

    # Review branch: review/{stage-label}/{feature-slug}
    # Branched off the feature branch; PR targets the feature branch (not main).
    # Merging the PR is what resumes the pipeline — no label needed.
    feature_slug   = re.sub(r"^feature/", "", branch)
    review_branch  = f"review/{stage_label.replace(' ', '-')}/{feature_slug}"

    pr_body = (
        f"## 🔍 AI pipeline paused — {stage_label} gate\n\n"
        f"The pipeline has completed the **{stage_label}** stage "
        f"and is awaiting your review before continuing.\n\n"
        f"### What to review\n"
        f"- `sdlc/design.md` — technical design\n"
        f"- `sdlc/code-review.md` — automated code review findings\n"
        f"- `sdlc/build-summary.md` — implementation notes\n"
        f"- `app/` — generated application code\n\n"
        f"### To continue the pipeline\n"
        f"**Merge this PR** into `{branch}`. "
        f"The merge will automatically trigger the pipeline to resume from the "
        f"**{stage_label}** checkpoint and proceed through the remaining stages.\n\n"
        f"### To reject\n"
        f"Close this PR without merging. The pipeline will not resume.\n\n"
        f"---\n_Gate: `{stage}` · review branch: `{review_branch}` · "
        f"checkpoint saved in `sdlc/`_\n"
    )
    pr_body_path = REPO_ROOT / "sdlc" / "gate-pr-body.md"
    pr_body_path.write_text(pr_body)

    def _r(cmd: str) -> subprocess.CompletedProcess:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if r.stdout.strip():
            print(f"  {r.stdout.strip()}")
        if r.returncode != 0 and r.stderr.strip():
            print(f"  ⚠️  {r.stderr.strip()}")
        return r

    _r('git config user.name "Claude AI 🤖"')
    _r('git config user.email "claude-sdlc@anthropic.com"')
    # Push the feature branch to GitHub first so it exists as the PR base.
    # The feature branch is only created locally by the workflow's "Fetch intent"
    # step and is never independently pushed, so GitHub doesn't know about it yet.
    _r(f"git checkout -b {branch} 2>/dev/null || git checkout {branch}")
    _r(f"git push origin HEAD:{branch}")
    _r(f"git checkout -b {review_branch}")
    _r(f"git add -- {' '.join(existing)}")
    _r(f'git diff --cached --quiet || git commit -m "🤖 [AI] {title[:65]} — gate: {stage_label}"')
    _r(f"git push origin HEAD:{review_branch}")

    _r('gh label create "in-review"    --color "e4e669" --description "Awaiting human review"     --force')
    _r('gh label create "ai-generated" --color "0075ca" --description "Generated by AI pipeline"  --force')
    _r(
        f'gh pr create '
        f'--title "🔍 [AI] {title[:62]}" '
        f'--body-file sdlc/gate-pr-body.md '
        f'--base {branch} --head {review_branch} --label ai-generated'
    )

    pr_url_r = subprocess.run(
        f"gh pr view {review_branch} --json url --jq '.url'",
        shell=True, capture_output=True, text=True,
    )
    pr_url = pr_url_r.stdout.strip() if pr_url_r.returncode == 0 else ""

    issue_number = os.environ.get("ISSUE_NUMBER", "")
    if issue_number:
        comment_path = REPO_ROOT / "sdlc" / "gate-comment.md"
        comment_path.write_text(
            f"⏸️ Pipeline paused at **{stage_label}** gate.\n\n"
            f"Review the PR{f': {pr_url}' if pr_url else ''} "
            f"(`{review_branch}` → `{branch}`) "
            f"then **merge it** to resume the pipeline.\n\n"
            f"_To reject: close the PR without merging._"
        )
        subprocess.run(
            f"gh issue comment {issue_number} --body-file sdlc/gate-comment.md",
            shell=True,
        )
        subprocess.run(
            f"gh issue edit {issue_number} --remove-label in-build --add-label in-review",
            shell=True,
        )
    print(f"\n  PR created. Pipeline paused at {stage_label} gate.")


def _effective_input(inp: int, cr: int, cc: int) -> int:
    """
    Compute billed-equivalent input tokens.

    prompt_tokens (inp) already includes cache_read and cache_creation tokens
    at face value (1× each). Adjust for actual billing multipliers:
      cache_read    tokens cost 0.10× → subtract 0.90× of their count
      cache_creation tokens cost 1.25× → add    0.25× of their count
    Output: equivalent token count at the base input rate.
    """
    return round(inp - cr * 0.90 + cc * 0.25)


def _write_token_report(sdlc: Path) -> None:
    """Print per-agent token usage and write sdlc/token-usage.md."""
    from agents.runner import MODEL_CONTEXT_WINDOW
    report = get_token_report()
    if not report:
        print("  No token data recorded (no API calls made).")
        return

    total_in       = sum(v["input_tokens"]                   for v in report.values())
    total_out      = sum(v["output_tokens"]                  for v in report.values())
    total_cr       = sum(v.get("cache_read_tokens",      0)  for v in report.values())
    total_cc       = sum(v.get("cache_creation_tokens",  0)  for v in report.values())
    total          = total_in + total_out
    has_cache      = total_cr > 0 or total_cc > 0
    total_eff_in   = _effective_input(total_in, total_cr, total_cc)

    rows = []
    hdr = f"\n  {'Agent':<20} {'Calls':>6} {'Input':>10} {'Output':>10} {'Total':>10} {'Peak ctx':>10}"
    sep = f"  {'─'*20} {'─'*6} {'─'*10} {'─'*10} {'─'*10} {'─'*10}"
    if has_cache:
        hdr += f" {'Cache read':>12} {'Cache write':>12} {'Eff. input':>12}"
        sep += f" {'─'*12} {'─'*12} {'─'*12}"
    print(hdr)
    print(sep)
    for agent, counts in report.items():
        inp      = counts["input_tokens"]
        out      = counts["output_tokens"]
        cr       = counts.get("cache_read_tokens",      0)
        cc       = counts.get("cache_creation_tokens",  0)
        eff      = _effective_input(inp, cr, cc)
        calls    = counts["api_calls"]
        peak     = counts.get("peak_ctx_tokens", 0)
        peak_pct = peak / MODEL_CONTEXT_WINDOW * 100
        flag     = " 🔴" if peak_pct >= 75 else (" ⚠️" if peak_pct >= 50 else "")
        line = (f"  {agent:<20} {calls:>6} {inp:>10,} {out:>10,} {inp+out:>10,} "
                f"{peak:>7,} ({peak_pct:4.1f}%){flag}")
        if has_cache:
            line += f" {cr:>12,} {cc:>12,} {eff:>12,}"
        print(line)
        rows.append((agent, calls, inp, out, cr, cc, eff, peak, peak_pct))
    print(sep)
    totline = f"  {'TOTAL':<20} {sum(r[1] for r in rows):>6} {total_in:>10,} {total_out:>10,} {total:>10,}"
    if has_cache:
        totline += f" {total_cr:>12,} {total_cc:>12,} {total_eff_in:>12,}"
    print(totline + "\n")

    # Write markdown report
    if has_cache:
        lines = [
            "# Token usage report\n",
            "> **Eff. input** = billed-equivalent input at base rate:"
            " `input − cache_read×0.90 + cache_write×0.25`"
            " (cache reads cost 0.10×, cache writes cost 1.25×).\n",
            "| Agent | API calls | Input tokens | Output tokens | Total tokens |"
            " Cache read | Cache write | Eff. input | Peak ctx tokens | Peak ctx % |",
            "|-------|----------:|-------------:|--------------:|-------------:|"
            "-----------:|------------:|-----------:|----------------:|-----------:|",
        ]
        for agent, calls, inp, out, cr, cc, eff, peak, peak_pct in rows:
            flag = " 🔴" if peak_pct >= 75 else (" ⚠️" if peak_pct >= 50 else "")
            lines.append(
                f"| {agent} | {calls} | {inp:,} | {out:,} | {inp+out:,} |"
                f" {cr:,} | {cc:,} | {eff:,} | {peak:,} | {peak_pct:.1f}%{flag} |"
            )
        lines.append(
            f"| **TOTAL** | **{sum(r[1] for r in rows)}** | **{total_in:,}** |"
            f" **{total_out:,}** | **{total:,}** | **{total_cr:,}** | **{total_cc:,}** |"
            f" **{total_eff_in:,}** | — | — |"
        )
    else:
        lines = [
            "# Token usage report\n",
            "| Agent | API calls | Input tokens | Output tokens | Total tokens | Peak ctx tokens | Peak ctx % |",
            "|-------|----------:|-------------:|--------------:|-------------:|----------------:|-----------:|",
        ]
        for agent, calls, inp, out, cr, cc, eff, peak, peak_pct in rows:
            flag = " 🔴" if peak_pct >= 75 else (" ⚠️" if peak_pct >= 50 else "")
            lines.append(
                f"| {agent} | {calls} | {inp:,} | {out:,} | {inp+out:,} | {peak:,} | {peak_pct:.1f}%{flag} |"
            )
        lines.append(
            f"| **TOTAL** | **{sum(r[1] for r in rows)}** | **{total_in:,}** | **{total_out:,}** | **{total:,}** | — | — |"
        )
    (sdlc / "token-usage.md").write_text("\n".join(lines) + "\n")
    print(f"  Report written to sdlc/token-usage.md")


def _write_pipeline_summary(
    sdlc: Path,
    intent: str,
    design_summary: str,
    build_summary: str,
    review_verdict: str,
    test_summary: str,
    qa_verdict: str,
) -> None:
    """Assemble sdlc/pipeline-summary.md used as the PR body for gh pr create."""
    title = _first_line(intent)

    def _badge(verdict: str, approved_label: str = "APPROVED") -> str:
        if not verdict:
            return "⏭️ skipped"
        return "✅ approved" if approved_label.lower() in verdict.lower() else f"❌ {verdict[:80]}"

    body = f"""## 🤖 AI-generated feature: {title}

### Design
{design_summary or "_no summary_"}

### Implementation
{build_summary or "_no summary_"}

### Code review
{_badge(review_verdict)}

### Tests
{test_summary or "_no summary_"}

### QA
{_badge(qa_verdict, "approved")}

---
See `sdlc/` for full agent reports.
"""
    (sdlc / "pipeline-summary.md").write_text(body)
    print("  Written sdlc/pipeline-summary.md")


def _is_approved(verdict: str) -> bool:
    """Return True if the last non-empty line of verdict is exactly 'APPROVED'."""
    for line in reversed(verdict.splitlines()):
        if line.strip():
            return line.strip().upper() == "APPROVED"
    return False


def _read_bugs_section(sdlc: Path) -> str:
    """Return the raw 'Implementation bugs found' section for Coder bug-fix context."""
    results_path = sdlc / "test-results.md"
    if not results_path.exists():
        return "(see sdlc/test-results.md)"
    content = results_path.read_text()
    match = re.search(
        r"##\s+Implementation bugs found\s*\n([\s\S]*?)(?=\n##|\Z)",
        content,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else "(see sdlc/test-results.md)"


def _tester_has_real_bugs(sdlc: Path) -> bool:
    """
    Parse sdlc/test-results.md directly to decide whether the Tester found
    real bugs, independent of the verdict word the model wrote.

    Returns True  (bugs exist)  — REGRESSION rows present OR non-empty bugs section.
    Returns False (report clean) — both sections are empty / None.

    Used as the authoritative gate so a model that incorrectly returns REJECTED
    despite a clean report does not trigger unnecessary Coder retries.
    """
    results_path = sdlc / "test-results.md"
    if not results_path.exists():
        return False  # no report → cannot confirm bugs, assume clean

    content = results_path.read_text()

    # Check "Implementation bugs found" section
    bugs_match = re.search(
        r"##\s+Implementation bugs found\s*\n([\s\S]*?)(?=\n##|\Z)",
        content, re.IGNORECASE,
    )
    if bugs_match:
        bugs_text = bugs_match.group(1).strip()
        if bugs_text and bugs_text.lower() not in ("none", "none found", "none."):
            return True

    # Check "Regression report" for REGRESSION-classified rows
    regr_match = re.search(
        r"##\s+Regression report\s*\n([\s\S]*?)(?=\n##|\Z)",
        content, re.IGNORECASE,
    )
    if regr_match and re.search(r"\|\s*REGRESSION\s*\|", regr_match.group(1), re.IGNORECASE):
        return True

    return False


def _init_project_overview(memory: "MemoryManager", config: dict) -> None:
    """
    Write project-overview.md on first pipeline run from config-derived facts.
    Skipped on subsequent runs (init_project_overview is a no-op when the file exists).
    """
    stack  = config.get("pipeline", {}).get("context", {}).get("stack") or {}
    target = config.get("target_repo") or {}
    repo   = target.get("repo", "unknown")
    branch = target.get("branch", "main")

    content = f"""# Project overview

## Target repository
- **Repo:** {repo} (branch: `{branch}`)
- **Local path:** {target.get("local_path", "./app")}

## Tech stack
- **Language:** {stack.get("language", "typescript")}
- **Framework:** {stack.get("framework", "angular")}
- **Runtime:** {stack.get("runtime", "node20")}
- **Test framework:** {stack.get("test_framework", "jasmine/karma")}
- **Backend:** {stack.get("backend", "spring-petclinic-rest")}
- **Deployment:** {stack.get("deployment", "azure_container_apps")}

## Notes
_Add permanent project context here that agents should always be aware of._
"""
    memory.init_project_overview(content)


def _update_patterns(memory: "MemoryManager", intent: str, design_summary: str, build_summary: str) -> None:
    """
    Append a dated entry to the pattern files after each successful run.
    Agents may overwrite these with richer content; this baseline ensures
    the files are never left as empty stubs.
    """
    from datetime import datetime, timezone
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title    = _first_line(intent)

    api_path = memory.root / "patterns" / "api-conventions.md"
    api_text = api_path.read_text() if api_path.exists() else "# API conventions\n"
    if "Populated by the pipeline" in api_text:
        api_text = "# API conventions\n\n_Entries appended after each pipeline run._\n"
    api_text += f"\n## {date_str} — {title[:60]}\n{design_summary[:300]}\n"
    api_path.write_text(api_text)

    err_path = memory.root / "patterns" / "error-handling.md"
    err_text = err_path.read_text() if err_path.exists() else "# Error handling\n"
    if "Populated by the pipeline" in err_text:
        err_text = "# Error handling\n\n_Entries appended after each pipeline run._\n"
    err_text += f"\n## {date_str} — {title[:60]}\n{build_summary[:300]}\n"
    err_path.write_text(err_text)


def _extract_code_review_findings(review_path: Path) -> list[str]:
    """Extract critical/major findings from sdlc/code-review.md findings table."""
    if not review_path.exists():
        return []
    findings = []
    in_findings = False
    for line in review_path.read_text().splitlines():
        if re.match(r"^##\s+Findings", line, re.I):
            in_findings = True
            continue
        if in_findings and re.match(r"^##\s+", line):
            break
        if in_findings and "|" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) < 4:
                continue
            severity = parts[0].lower()
            if "critical" in severity or "major" in severity:
                issue = parts[3] if len(parts) > 3 else parts[-1]
                if issue and not re.match(r"^[-:| ]+$", issue) and "severity" not in issue.lower():
                    findings.append(issue)
    return findings


def _extract_qa_findings(qa_path: Path) -> list[str]:
    """Extract gap findings from the '## Gaps identified' section of sdlc/qa-review.md."""
    if not qa_path.exists():
        return []
    findings = []
    in_gaps = False
    for line in qa_path.read_text().splitlines():
        if re.match(r"^##\s+(Gaps|Coverage gaps|Missing coverage|Changes Required)", line, re.I):
            in_gaps = True
            continue
        if in_gaps and re.match(r"^##\s+", line):
            break
        if in_gaps and line.startswith("- ") and line[2:].strip().lower() != "none":
            findings.append(line[2:].strip())
    return findings


def _record_rejection(
    memory: "MemoryManager",
    artifact_path: Path,
    reviewer: str,
    target: str,
) -> None:
    """Extract findings from a review artifact and record them in rejection-patterns.md."""
    from datetime import datetime, timezone
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if reviewer == "Code Reviewer":
        findings = _extract_code_review_findings(artifact_path)
    else:
        findings = _extract_qa_findings(artifact_path)
    if findings:
        memory.record_rejection_findings(reviewer, target, findings, run_date)
        print(f"  📝 Recorded {len(findings)} rejection finding(s) in memory/patterns/rejection-patterns.md")


def _recurring_patterns_note(memory: "MemoryManager", target: str) -> str:
    """Return a prompt section listing recurring rejection patterns for Coder or Tester."""
    path = memory.root / "patterns" / "rejection-patterns.md"
    if not path.exists():
        return ""
    content = path.read_text()
    if "## Recurring" not in content or "No recurring issues yet" in content:
        return ""
    recurring_section = content.split("## Recurring", 1)[1].split("## Observation log")[0]
    marker = f"### {target} ←"
    if marker not in recurring_section:
        return ""
    block = recurring_section.split(marker, 1)[1].split("###")[0].strip()
    if not block:
        return ""
    return (
        f"\n\n## ⚠️ Recurring rejection patterns — you MUST avoid these\n"
        f"These issues caused rejections in multiple previous runs. "
        f"Check your output against each one before finishing:\n"
        f"{block}"
    )


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return "feature from intent.md"


def _banner(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")

def load_manifest(workspace: str) -> dict:
    manifest_path = os.path.join(workspace, "deliverables.json")
    if not os.path.exists(manifest_path):
        raise RuntimeError(
            "Designer did not produce deliverables.json. "
            "Cannot proceed without a deliverables manifest."
        )
    with open(manifest_path) as f:
        return json.load(f)


def check_deliverables(manifest: dict, workspace: str) -> list[str]:
    missing = []
    for item in manifest["deliverables"]:
        full_path = os.path.join(workspace, item["path"])
        if not os.path.exists(full_path):
            missing.append(item["path"])
    return missing


def build_missing_feedback(missing: list[str], manifest: dict) -> str:
    items = [d for d in manifest["deliverables"] if d["path"] in missing]
    lines = [f"- {d['path']}: {d['description']}" for d in items]
    return (
        "The following required files are missing. "
        "You must create them before finishing:\n" + "\n".join(lines)
    )


def build_coder_context(manifest: dict | None) -> str:
    if not manifest:
        return ""
    items = [d for d in manifest["deliverables"] if d.get("agent") == "Coder"]
    lines = [f"- {d['path']} ({d['type']}): {d['description']}" for d in items]
    return (
        "## Your Required Deliverables\n"
        "You must create or modify every file listed below. "
        "Do not finish until all of them exist on disk.\n\n"
        + "\n".join(lines)
    )


def build_tester_context(manifest: dict | None) -> str:
    if not manifest:
        return ""
    items = [d for d in manifest["deliverables"] if d.get("agent") == "Tester"]
    lines = [f"- {d['path']} ({d['type']}): {d['description']}" for d in items]
    return (
        "## Your Required Deliverables\n"
        "You must create every test file listed below. "
        "Do not finish until all of them exist on disk.\n\n"
        + "\n".join(lines)
    )


def _write_unresolved_findings(sdlc: Path, agent: str, missing: list[str]) -> None:
    path = sdlc / f"{agent.lower()}-unresolved.md"
    lines = [
        f"# Unresolved findings — {agent}\n",
        "The following deliverables were never produced after all retries:\n",
    ] + [f"- `{m}`" for m in missing]
    path.write_text("\n".join(lines) + "\n")
    print(f"  Unresolved findings written to {path.name}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted")
        sys.exit(1)
    except Exception as exc:
        print(f"\n❌ Pipeline failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
