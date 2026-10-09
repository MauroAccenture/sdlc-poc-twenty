# Rejection patterns

_Automatically updated after each pipeline rejection._
_Recurring issues (≥ 2 separate runs) are injected into agent prompts._

## Recurring (≥ 2 runs) — injected into prompts

### Coder ← Code Reviewer
- [2×] Every valid queue message is dispatched to `_ConfiguredHandler`, whose `run` unconditionally raises `RuntimeError`. Consequently no pipeline activity runs, successful messages are never acknowledged, and messages retry indefinitely. The design requires the download → analyse → research → generate → submit → notify pipeline.
- [2×] The implementation does not renew visibility for long-running jobs or implement poison handling, despite both being explicit worker requirements. A 900-second visibility timeout alone can produce duplicate processing for long photo/AI/API work.
- [2×] On a 401, `_request` recursively calls itself after `kwargs.pop("headers", {})` has already removed the caller headers. The retry therefore drops `Idempotency-Key` (and any other request-specific header). A transient token-expiry response on create can then retry a non-idempotent POST without its deduplication key and create a duplicate draft.
- [2×] The deployment writes the literal `SET_WITH_AZURE_CLI` into both production secrets. Deploying/redeploying this module can overwrite valid operator-provisioned credentials, and the application will fail until a separate manual repair. Secret values must not be represented as committed deployment values.
- [2×] The activity performs one undifferentiated search, sends no active/sold status selection, always reports `source="sold"`, and never prefers sold results over active results. This does not implement the required comparable-price behavior and can mislabel active prices as sold.
- [2×] The build summary says the Vinted taxonomy was deleted, but the file still exists (as an empty placeholder). The repository also still contains `app/playwright_service/` and `app/infra/modules/playwright_app.bicep`, contrary to the required complete removal.

### Tester ← QA Engineer
- [2×] Price research lacks passing tests for active fallback, empty comparables/low-confidence fallback, outlier removal, filtering by category/brand/size/condition, sample count, and rounding edge cases.
- [2×] Graph/API required success and error scenarios did not execute; add an isolated test environment or dependency fixture so these are runnable without the legacy Dropbox package.
- [2×] Code-review risks are only partially covered: pipeline/model/Tradera tests pass, but checkpoint/manual webhook callers and logical-vs-filesystem `folder_path` handling are represented only by import-error tests, not passing regression coverage.
- [2×] No Graph/OpenAI/provider retry-boundary tests, full browser field integration, Azurite round trips, Bicep compilation, Docker startup, or measured per-module coverage threshold.
- [2×] Folder parser, model validation, checkpoint crash/resume, notification payload, and price three-comparable cases are incomplete.

## Observation log

### 2026-10-05 — QA Engineer → Tester
- `azure.yaml` still contains the removed `playwright_service` entry; the feature-owned security test fails on this defect.
- No test for manual jobs disabled returning 404.
- No test for Key Vault unavailable/missing credentials and eventual poison behavior.
- No explicit 429 handling or `Retry-After` assertion.
- No test for photo upload failure retry/poison behavior.
- No test for notification retry isolation after a draft has been created.
- No Azurite queue consumption, visibility renewal, or poison-queue test.
- No live Docker Compose startup or Bicep compilation verification reported.
- The documented `/jobs` endpoint is not directly tested; tests exercise the legacy `/pipeline/run` route.
- The empty-title validation case is not explicit, and malformed manual-job payload coverage is incomplete.
- Code-review risk coverage: QueueMessage/checkpoint and pipeline compatibility are covered by `test_pipeline_runs_in_order_and_checkpoints` and API/contract tests; Tradera model serialization/semantics are covered by the model and listing contract tests; logical folder-path handling is only partially covered by the unsafe-parent-path test; the targeted Tradera tests are represented in the Test cases table. These gaps are informational unless also mandated by the design strategy.

### 2026-10-05 — QA Engineer → Tester
- Required design testing-strategy flows are missing: token refresh-before-expiry; timeout, connection, 429, 401, 403, and 404 handling; bounded retry/poison behavior; and secret-unavailability behavior.
- Required photo contract coverage is incomplete: successful multipart upload, content type/size behavior at boundaries, EXIF handling inside the client/activity path, and upload failure retry/poison behavior.
- Price research lacks passing tests for active fallback, empty comparables/low-confidence fallback, outlier removal, filtering by category/brand/size/condition, sample count, and rounding edge cases.
- Taxonomy tests do not cover every required clothing category, XS through XXL, shoe sizes 35 and 47 plus the complete 35–47 range, primary colors, and all condition codes.
- Analysis mapping to Tradera fields is not tested.
- Graph/API required success and error scenarios did not execute; add an isolated test environment or dependency fixture so these are runnable without the legacy Dropbox package.
- Queue integration against Azurite, poison behavior, Bicep compilation, Docker Compose startup, worker connection, and repository-wide removal/security checks are untested.
- Code-review risks are only partially covered: pipeline/model/Tradera tests pass, but checkpoint/manual webhook callers and logical-vs-filesystem `folder_path` handling are represented only by import-error tests, not passing regression coverage.

### 2026-10-05 — Code Reviewer → Coder
- Every valid queue message is dispatched to `_ConfiguredHandler`, whose `run` unconditionally raises `RuntimeError`. Consequently no pipeline activity runs, successful messages are never acknowledged, and messages retry indefinitely. The design requires the download → analyse → research → generate → submit → notify pipeline.
- The implementation does not renew visibility for long-running jobs or implement poison handling, despite both being explicit worker requirements. A 900-second visibility timeout alone can produce duplicate processing for long photo/AI/API work.
- On a 401, `_request` recursively calls itself after `kwargs.pop("headers", {})` has already removed the caller headers. The retry therefore drops `Idempotency-Key` (and any other request-specific header). A transient token-expiry response on create can then retry a non-idempotent POST without its deduplication key and create a duplicate draft.
- The deployment writes the literal `SET_WITH_AZURE_CLI` into both production secrets. Deploying/redeploying this module can overwrite valid operator-provisioned credentials, and the application will fail until a separate manual repair. Secret values must not be represented as committed deployment values.
- The module declares storage and the two Tradera secrets but does not declare the required `graph-client-id`, `graph-client-secret`, `graph-client-state`, and `notification-webhook-url` secrets. This can break the unchanged Graph/webhook/notification paths after deployment and does not meet the required post-migration secret set.
- Photo references are read directly with `Path(reference).read_bytes()`. The worker wiring shown does not materialize/download queue photo references into local paths, and `_ConfiguredHandler` never does so. In a normal queue deployment this makes submission fail for the documented OneDrive photo references.

### 2026-10-05 — Code Reviewer → Coder
- The worker only logs that it is ready and waits forever; it does not connect to Azure Storage Queue, poll messages, execute download/analyse/research/generate/submit/notify activities, renew visibility, or handle poison messages. This disables the core application pipeline.
- `_lookup()` lowercases string keys, but `SIZES` is keyed by uppercase `XS`–`XXL`. Consequently every alphabetic size (XS through XXL) raises `ValueError`; normal listing generation cannot succeed.
- `Condition(result.get("condition", "GOOD"))` uses `GOOD`, while the enum values are `USED_GOOD`, etc. The default path always raises, and typical lower-case AI output such as `good` also raises.
- The Tenacity predicate retries only timeout/connect exceptions. 429 and 5xx responses are converted to `HTTPStatusError`, which is not in the predicate, so they are not retried. `Retry-After` is also ignored. Token acquisition is not covered by the transient retry policy either.
- The activity performs one undifferentiated search, sends no active/sold status selection, always reports `source="sold"`, and never prefers sold results over active results. This does not implement the required comparable-price behavior and can mislabel active prices as sold.
- The Key Vault is still named `kv-vinted-*`, and the module creates only a storage connection secret. The two Tradera declarations in `main.bicep` are `existing` resources rather than secret declarations/values and do not provision or wire `tradera-app-id` and `tradera-app-key`. A fresh deployment therefore cannot satisfy the required Tradera secret setup.
- The build summary says the Vinted taxonomy was deleted, but the file still exists (as an empty placeholder). The repository also still contains `app/playwright_service/` and `app/infra/modules/playwright_app.bicep`, contrary to the required complete removal.
- The activity opens each `photo_refs` value as a local filesystem path, while the pipeline contract carries photo blobs/references and the download activity boundary is not connected because `worker/main.py` does not orchestrate it. In the deployed worker this will fail unless paths happen to exist in the container.
- The request serializes `inactive` and `fixed_price`, but there is no explicit endpoint/API-version mapping or validation that the upstream response represents an inactive draft. More importantly, create retries are not idempotency-safe despite the design requiring bounded create retry and an idempotency/correlation key.

### 2026-10-01 — QA Engineer → Tester
- No test protects against the public API exposing or impersonating Playwright publishing, one of the Code Reviewer’s highest-risk areas.
- No durable cross-replica or restart idempotency test for Graph/manual queue requests; current tests only exercise an in-memory set.
- No Azure SDK exception test for webhook queue failures; only manual-trigger `RuntimeError` mapping is covered.
- No Key Vault tests can pass until the missing `azure-keyvault-secrets` dependency is declared and importable.
- No Graph/OpenAI/provider retry-boundary tests, full browser field integration, Azurite round trips, Bicep compilation, Docker startup, or measured per-module coverage threshold.
- EXIF coverage does not embed GPS/device metadata as required by the design.
- Folder parser, model validation, checkpoint crash/resume, notification payload, and price three-comparable cases are incomplete.
- The zero-comparable test expects `ValueError`, while the design requires the pipeline to continue with `insufficient_data` confidence.
- The retrieved `test_qa_gaps.py` appears truncated; the Tester should restore/verify the complete file and report its actual collection and execution status.

### 2026-10-01 — QA Engineer → Tester
- Add a test for the exact design Graph notification (`resource: drives/me/items/root`), including path resolution, root enforcement, and the exact queue payload.
- Add mixed-batch webhook tests and durable duplicate tests across concurrent requests, process restart, and multiple API replicas.
- Add Azure `AzureError`/`HttpResponseError` queue failure tests for both webhook and manual trigger, plus queue message compatibility with the worker and poison-message behavior.
- Add tests proving secrets are loaded by Key Vault using configured secret names, are cached, and never appear in environment/configuration or error responses.
- Remove/secure any public publish compatibility route and test that only the private Playwright service can publish; add authenticated successful publish behavior and verify `confirm: true` invokes the controlled operation.
- Add `/session` authenticated/expired tests and Playwright `/health` tests.
- Add explicit empty, unsupported, and ambiguous folder-size tests, including all required size variants.
- Add price-research tests for invalid prices, accessories, wrong condition, outliers, exact percentiles, and 0/1/2/3 comparables with insufficient-data confidence.
- Add model tests for invalid enum values, non-empty photo validation, and checkpoint serialization round-trip.
- Replace the empty-EXIF test fixture with JPEG GPS latitude/longitude and camera make/model tags and assert all are removed.
- Add retry-boundary and redacted-error tests for Graph/OpenAI/provider adapters, plus renewer success and failure-alert tests.
- Add rate-limiter rejection/window tests, real randomized delay-bound checks, every-field form-fill assertions, and cleanup-only-after-success checks.
- Add Azurite round-trip, Bicep build, Docker Compose, application startup/import, and per-module coverage-threshold checks required by the design.

### 2026-10-01 — Code Reviewer → Coder
- The public API app exposes `/publish` without any authentication. Any caller can submit `confirm: true` and receive a successful published response. The design only permits this operation on the private Playwright service and requires the internal service token. The implementation is also validation-only rather than performing the publish operation.
- `GRAPH_CLIENT_STATE`, `MANUAL_TRIGGER_KEY`, and `INTERNAL_SERVICE_TOKEN` are read as secret values from environment variables. The design requires these values to be fetched from Key Vault (environment variables may contain only secret names/references), and the security model explicitly disallows secrets in environment configuration.
- Idempotency is only an in-memory set. It is lost on restart, does not coordinate across API replicas, and two concurrent calls can both pass the membership check before either adds the ID. This does not satisfy the design's duplicate-notification guarantee and can enqueue duplicate work.
- The documented notification example uses `resource: "drives/me/items/root"`, but the code only accepts a nonstandard `folder_path` field or resources containing `/folder/`. A valid Graph notification in the specified shape is therefore rejected with `invalid_notification`, and no changed path is enqueued.
- The handlers catch only `OSError` and `RuntimeError`. Azure Queue SDK failures normally surface as Azure SDK exceptions (such as `AzureError`/`HttpResponseError`), so an unavailable queue can become an unhandled 500 rather than the documented `503 storage_unavailable`.

### 2026-10-01 — Code Reviewer → Coder
- The route returns `202` without sending a `FolderRequest` to the Storage Queue. This reports acceptance even though no pipeline run can occur, violating the `/pipeline/run` contract and making manual backfills non-functional.
- `queue` is initialized to `None` and this file contains no startup/dependency wiring that assigns a real `QueueStore`. In a deployed process every valid webhook therefore returns `503 Queue is unavailable`; there is no functioning API path to enqueue work.
- `payload` is annotated `Any` and only `payload.get(...)` is attempted. A valid JSON scalar, list, or `null` raises `AttributeError`, which is not caught and becomes an unstructured 500 instead of the specified `400 invalid_notification` response.
- The implementation deduplicates resources only within one HTTP request (`set`), but does not use the deterministic run ID or any persistent/idempotent queue mechanism. Repeated Graph notifications can create multiple queue messages and duplicate pipeline runs, contrary to the design's duplicate/idempotency requirement.
- Missing `confirm` is handled by FastAPI's default validation response, not the common `{error:{code,message}}` envelope. The design explicitly requires consistent JSON API errors and a publish confirmation error contract for missing/false confirmation.

### 2026-10-01 — Code Reviewer → Coder
- `JSONResponse(202, {...})` passes the status code as content and a dict as the positional status argument; the valid webhook path raises instead of returning the required 202 response. The queue is also never initialized, so the normal path returns 503.
- The manual route validates and returns an ID but never enqueues a `QueueMessage`, so it cannot trigger a pipeline. It has the same invalid positional `JSONResponse(202, {...})` construction. It also relies on environment secret values despite the design requiring Key Vault-loaded secrets.
- The implementation accepts arbitrary resource strings as folder paths and does not parse/validate every notification as specified. It does not use the shared handler, does not produce the required normalized folder identity, and does not protect against invalid resource/path inputs.
- The six activities are called as `activity(value)`, but `download_photos` requires `(run_id, folder_path, graph, blob)` and subsequent activities require different dependencies. The pipeline therefore cannot run. Checkpointed dicts are not reconstructed into Pydantic models on resume, and no retry boundaries are implemented.
- The worker exits immediately with `Configure QueueStore...` rather than starting a queue consumer. Exceptions are limited to `ValueError`/`RuntimeError`, messages are not visibility-updated or dead-lettered, and storage errors can kill the loop.
- It ignores MIME type/image filtering, uses untrusted Graph item IDs directly in blob names, does not implement delta paging or changed-subfolder semantics, and does not ensure EXIF stripping is applied to a valid image before upload.
- This is not an OAuth Graph client: it receives a token directly, does not acquire client-credential tokens from Key Vault/MSAL, ignores `folder_path`, and `create_subscription`/`renew_subscription` issue GET requests instead of the required POST/PATCH operations. Delta paging and subscription payloads are absent.
- The deployment omits Blob containers, Queue, File Share, role assignments, Key Vault secret references, Container Apps probes/env configuration, Azure Files mount, and the renewer Container Apps Job. All listed modules are comments/placeholders and are not wired. `renewerImage` is unused. This fails the core deployment acceptance criteria.
- No Playwright browser/context is created, no Vinted form is accessed, photo blobs are ignored, only five hard-coded field names are delayed, CAPTCHA is never detected, and `RateLimiter` is never used. This cannot create a draft or satisfy the required serialized/rate-limited browser behavior.
- It only calls an abstract `create_draft(listing)`. It does not pass photo references, delete staged photos only after success, or write the required listing-history record.
- It does not filter wrong conditions, accessories, or statistical outliers as required. It reports `medium` never (and can fail on malformed provider objects before validation), and its percentile behavior is not the specified p25/p75 behavior for small datasets.
- The parser treats the final word before size as the model and all earlier words as brand, so `Dressmann Jean Shirt Size M` becomes brand `Dressmann Jean`, model `Shirt`. It does not support the documented numeric shoe and `W32 L30` cases robustly, does not reject ambiguity as designed, and the OpenAI call is not schema-constrained or taxonomy-mapped.
- The renewer is not executable: it raises a placeholder exception rather than constructing adapters, renewing/persisting subscription state, and exiting successfully.
- Documentation is only a two-line overview; Compose lacks Azurite configuration, service environment/dependencies/health checks, and the parameters file has no location/environment or resource configuration. The requested manual deployment, Graph registration, session bootstrap, and operational safety instructions are absent.
- The design explicitly lists unit and integration tests and requires API, pipeline, EXIF, Playwright, Azurite, and coverage verification. Only an empty `conftest.py` exists; the build summary says tests are intentionally deferred. This is not a complete initial implementation and leaves critical behavior unverified.
- Required security and contract constraints are missing: photo references have no traversal validation, `currency` is unconstrained, URLs/IDs are not validated, and checkpoint step/output semantics are loose.

### 2026-10-01 — Code Reviewer → Coder
- The executable starts `poll(None, None)`, then calls `queue.receive()` and `process(message)`, so the worker crashes immediately instead of polling Azure Storage Queue.
- `run_pipeline` only accepts an arbitrary activity dictionary, never writes the initial checkpoint or final status, and calls `record_step` only after a step. It does not instantiate/call the six real activities, has no retry boundaries, and resume values are not sufficient to reconstruct the typed inputs for the next activity.
- Checkpoints are stored in a process-local dictionary, not Blob Storage, and BlobStore lacks the `upload` operation used by `download_photos` and lacks download/list support required by the design. A restart loses all state, violating crash resume and idempotency.
- It calls a nonexistent `BlobStore.upload`, does not include the configured staging container, does not reject an empty photo batch, and assumes a download URL key without robust validation.
- The required OpenAI vision call, folder metadata fallback, structured response validation, observed defects/measurements handling, and taxonomy mapping are absent. Parsing takes only the first token as the brand, so multi-word brands are not supported as required.
- The activity does not query a provider and only filters category/positive prices. It does not remove accessories, wrong conditions, or statistical outliers and does not implement the documented percentile/confidence semantics.
- This is not the required OAuth client-credential adapter: it accepts a token directly and implements neither `create_subscription` nor `renew_subscription`. Delta pagination/root constraints are also absent.
- The executable calls `renew(None, None)` and necessarily fails with an attribute error; it does not load credentials, create versus renew subscriptions, persist subscription metadata, or provide a successful valid-credentials path. It also re-raises after alerting, contrary to the claimed completion scaffold.
- SessionManager always reports `expired` because `authenticated` is never set, and it only creates a directory rather than loading a Playwright persistent context. `fill_draft` does not use a browser, download blobs, fill fields, detect CAPTCHA, serialize browser requests, or verify it stops before publish. Consequently `/draft` can never succeed.
- `CLIENT_STATE`, `MANUAL_KEY`, and `TOKEN` are hardcoded source constants. This violates the Key Vault/managed-identity security model and makes production authentication impossible.
- The managed identity module creates an identity but defines no role assignments; its storage input is unused. Apps/jobs use no shared user-assigned identity, Key Vault is not connected, secret references are absent, and OpenAI is not provisioned at all. The deployment therefore cannot provide the resources and access model in the design.
- FastAPI `HTTPException(detail={"error": ...})` serializes as `{ "detail": { "error": ... } }`, not the required `{ "error": { ... } }`. The app also lacks a global exception handler for the specified internal-error envelope and does not perform the complete notification/resource validation contract.
- The adapter requires and sends an API key, contrary to the design's requirement that all Azure SDK/service access use managed identity and the Bicep Cognitive Services OpenAI role.
- None of the design-listed unit or integration test files exist. The build summary explicitly reports that pytest found no tests, so required EXIF, API, pipeline, Playwright, Azurite, model, pricing, checkpoint, and coverage verification is absent.
