# Code review

## Verdict
APPROVED

## Summary
The change is limited to the design-approved shared worker models file and adds the requested `QueueMessage` compatibility envelope without removing or weakening the Tradera contracts. The model provides typed metadata, timestamp defaults, basic folder-path validation, and remains compatible with the existing `ListingJob`, listing, pricing, draft, and pipeline contracts. The reported targeted integration and Tradera feature tests pass, and no new dependencies or unplanned files were introduced.

## Findings

| Severity | File | Line / area | Issue | Suggested fix |
|---|---|---|---|---|
| Minor | `app/worker/shared/models.py` | `QueueMessage` fields | `run_id` and `source` are accepted as arbitrary strings, including empty or whitespace-only values. If these values are used for correlation, checkpointing, or audit logs, malformed metadata can propagate. | Add appropriate non-empty constraints/normalization if callers require these fields to be meaningful; otherwise document that optional `run_id` may be absent and validate `source` against supported values. |
| Minor | `app/worker/shared/models.py` | `QueueMessage.valid_folder_path` | The validator rejects parent traversal segments and backslashes but permits absolute paths and empty/dot path segments. That is acceptable only if the value is always a logical OneDrive path and never passed to local filesystem operations. | If consumers can use this as a filesystem path, reject absolute paths, empty segments, and `.` segments; otherwise document the logical-path assumption. |

## Checklist

- [x] Implementation matches the design exactly for the affected shared contracts
- [x] All endpoints, parameters, and response shapes are correct or unaffected
- [x] All specified model validation/error cases are handled
- [x] No silent failures or swallowed exceptions
- [x] All inputs added by this change have validation before use
- [x] Passwords/secrets are never logged or returned
- [x] JWT validation requirements are unaffected; no protected route was changed
- [x] No SQL string interpolation
- [x] No sensitive data in error messages
- [x] Code matches the existing style and uses clear model names
- [x] Functions are small and single-purpose
- [x] No dead code or unnecessary complexity introduced
- [x] Type hints are present and appropriate
- [x] A new team member could understand the compatibility contract
- [x] No problematic magic numbers/strings introduced
- [x] Error messages are clear and actionable
- [x] Coder only modified files listed in the design scope
- [x] No out-of-scope file changes are present
- [x] Regression risk areas are identified

## Regression risk areas

- Existing checkpoint/manual webhook callers that construct or serialize `QueueMessage`, especially folder paths and optional `run_id` values.
- Queue/pipeline integration points that distinguish the legacy `QueueMessage` envelope from the canonical `ListingJob` contract.
- Pydantic serialization/deserialization of `ListingPayload`, `PriceResearch`, and `DraftResult`, including SEK-only and inactive fixed-price validation.
- Any consumer that treats `folder_path` as a local filesystem path rather than a logical OneDrive path.
- The targeted pipeline and Tradera feature tests should be retained in regression coverage; the build summary reports both passing.
