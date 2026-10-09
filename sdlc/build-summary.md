# Build summary

## Summary
Restored the missing `QueueMessage` shared contract required by the existing checkpoint-based pipeline and API/manual webhook integration while preserving the Tradera-first contracts. The compatibility envelope validates folder paths, carries run/source metadata, and serializes cleanly alongside the new `ListingJob` contract, allowing the pipeline integration and Tradera feature tests to collect and pass.

## Files changed

| File | Action | Description |
|---|---|---|
| `app/worker/shared/models.py` | Modify | Added the `QueueMessage` compatibility model and retained/typed the Tradera listing, job, pricing, draft, notification, health, and pipeline contracts. |

## Unplanned file changes

None.

## New dependencies added

None.

## New environment variables required

None.

## Assumptions made

- Existing checkpoint/manual-trigger callers still require a folder-oriented `QueueMessage`; it is retained as a compatibility contract rather than replacing the Tradera `ListingJob` queue schema.
- Legacy Playwright/Vinted tests are outside the Tradera replacement scope and were not changed.
- Ruff is not installed in the execution environment, so the configured linter could not be run.

## Verification

- Targeted tests: `python -m pytest tests/integration/test_pipeline.py tests/unit/test_tradera_feature.py -q` — 7 passed.
- Linter: attempted `ruff check ...`; unavailable because `ruff` is not installed.
