# Create first version of new application item-lister

Date: 2026-10-01T15:49:32.370752+00:00

## What was built
Implemented the reviewed API and Playwright fixes, including queue wiring, manual and webhook enqueueing, idempotency, malformed-payload handling, Graph path validation, and consistent error responses.

## Design
Designed the complete greenfield Vinted AutoLister architecture, including application services, Azure infrastructure, APIs, data contracts, deployment files, error handling, security, testing, and operational safeguards.

## Code review
**Verdict:** ⚠️ CHANGES REQUESTED
The review found multiple blocking issues, including an unauthenticated public `/publish` endpoint, secrets read directly from environment variables instead of Key Vault, process-local queue idempotency, rejection of the documented Graph notification shape, and incomplete Azure storage error handlin

## QA
**Verdict:** ⚠️ COVERAGE INSUFFICIENT
A = 0
B = 3
Three implementation bugs were identified by the new QA coverage: documented Graph payload rejection, incorrect price-condition filtering, and missing Azure Key Vault dependency.
REJECTED

## Links
- [[architecture-decisions]]
- [[api-conventions]]
