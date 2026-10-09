# QA review

## Verdict
REJECTED

## Regression verdict
CLEAN — no regressions

The Tester classified all failures outside the feature-owned test table as PRE-EXISTING under the empty-baseline rule. No failure is classified as REGRESSION in the regression report. The failed feature-owned deployment/security test is treated as an implementation bug, not a regression classification, and is recorded below.

## Coverage audit

| Endpoint / scenario | Tested? | Test name |
|---|---|---|
| `GET /health` success and safe response | Yes | `test_health_is_public_and_safe` |
| `POST /webhooks/graph` validation token returns plain text 200 | Yes | `test_graph_handshake_echoes_plain_text` |
| Graph validation token missing/invalid | Yes | `test_graph_rejects_malformed_notifications` |
| Graph notification with invalid client state | Yes | `test_graph_rejects_wrong_client_state` |
| Graph notification accepted with 202 | Yes | `test_documented_graph_payload_is_accepted_and_enqueues_exact_message` |
| Duplicate Graph notification is idempotent | Yes | `test_graph_enqueues_root_folder_and_is_idempotent` |
| Graph notification outside configured root rejected | Yes | `test_graph_ignores_folder_outside_configured_root` |
| `POST /jobs` / manual enqueue valid request | Partial | `test_manual_trigger_requires_key` exercises the legacy `/pipeline/run` route rather than the design's `/jobs` contract |
| Manual jobs missing/invalid authentication | Yes | `test_manual_trigger_requires_key` |
| Malformed manual job / Pydantic 422 | Partial | `test_manual_trigger_rejects_unsafe_path` covers unsafe path validation, but not the documented malformed payload cases |
| Manual jobs disabled returns 404 | No | — |
| Duplicate source item returns 202 without a second enqueue | Yes | `test_manual_trigger_duplicate_is_accepted_without_second_message` |
| OAuth credential retrieval from Key Vault | Yes | `test_authentication_fetches_keyvault_credentials_and_caches_token` |
| OAuth token caching | Yes | `test_authentication_fetches_keyvault_credentials_and_caches_token` |
| OAuth refresh before expiry | Yes | `test_expiring_token_is_refreshed_before_use` |
| OAuth authentication failure / 401 refresh-once behavior | Yes | `test_unauthorized_request_refreshes_token_once` |
| Key Vault unavailable or missing secret -> retry/poison | No | — |
| `create_listing` creates inactive fixed-price draft | Yes | `test_tradera_client_creates_inactive_fixed_price_draft`; `test_create_listing_sends_inactive_fixed_price_and_idempotency_key` |
| Create-listing response maps draft ID and URL | Yes | `test_create_listing_sends_inactive_fixed_price_and_idempotency_key` |
| No activation/publish call | Yes | `test_create_listing_sends_inactive_fixed_price_and_idempotency_key` |
| Tradera 400/403/404 rejected without retry | Yes | `test_transient_server_errors_are_retried_but_bad_request_is_not`; `test_forbidden_and_missing_resources_are_not_retried` |
| Tradera timeout/connection/429/5xx transient retry and bounded attempts | Partial | `test_timeout_and_connection_errors_are_bounded_retries`; `test_transient_server_errors_are_retried_but_bad_request_is_not` cover timeout, connection, and 5xx, but no explicit 429/Retry-After assertion |
| `upload_photo` multipart success and URL/content type | Yes | `test_photo_upload_sends_multipart_content_type_and_listing_url` |
| Photo invalid bytes/empty and oversized input | Yes | `test_search_passes_filters_and_upload_rejects_empty_or_oversized_photo` |
| Photo upload failure -> retry/poison | No | — |
| Search active/sold filters | Yes | `test_search_passes_filters_and_upload_rejects_empty_or_oversized_photo`; `test_price_research_prefers_sold_and_uses_sek_rounding` |
| Price research prefers sold prices | Yes | `test_price_research_prefers_sold_and_uses_sek_rounding` |
| Price research active fallback | Yes | `test_price_research_falls_back_to_active_then_category_fallback` |
| Price research empty-result category fallback and low confidence | Yes | `test_price_research_falls_back_to_active_then_category_fallback` |
| Price outlier removal, category/condition filtering, and rounding | No (feature run) | `test_price_research_filters_accessories_wrong_condition_outliers_and_uses_p75` is reported as pre-existing and failing under the empty-baseline rule |
| Invalid taxonomy mapping -> poison | Yes | `test_unknown_taxonomy_is_rejected` |
| Required category/size/color/condition mappings | Yes | `test_all_required_taxonomy_values_have_explicit_mappings`; `test_required_taxonomy_maps_case_insensitively` |
| Analysis maps extracted attributes to Tradera fields | Yes | `test_analysis_maps_all_product_attributes_to_tradera_fields` |
| Listing generation title limit and non-empty content | Yes | `test_listing_generation_uses_price_and_limits_title`; `test_generator_limits_title_and_sets_sek` |
| Listing generation SEK, fixed-price, inactive semantics | Yes | `test_generator_limits_title_and_sets_sek`; `test_listing_contract_enforces_tradera_semantics` |
| Listing model rejects empty/overlong title and non-positive price | Partial | `test_models_validate_positive_price_and_title` covers overlong title and zero price; no explicit empty-title assertion |
| Listing model rejects non-SEK and active/non-fixed values | Yes | `test_listing_contract_enforces_tradera_semantics` |
| `submit_draft` directly creates listing then uploads every photo | Yes | `test_submit_draft_creates_then_uploads_all_photos` |
| Notification includes draft URL only after successful submission | Yes | `test_notification_receives_draft_url_only_after_submission` |
| Notification failure isolated and retried without recreating listing | No | — |
| Queue serialization/consumption, visibility renewal, and poison behavior against Azurite | No | — |
| Bicep compilation and infrastructure deployment contract | Partial | `test_deployment_and_repository_security_contracts` checks text contracts but does not compile Bicep |
| Docker Compose startup and health/worker queue connection | No | — |
| Repository absence of Playwright/Vinted artifacts and dependencies | Failed | `test_deployment_and_repository_security_contracts` — detects `playwright_service` in `azure.yaml` |

### Error-case accounting

There are 15 documented error cases in design section 7 when the Tradera 400/403/404 row is treated as one documented case. Eight are clearly covered: invalid Graph state/validation, malformed/unsafe manual input, duplicate source item, Tradera authentication failure, Tradera 400/403/404, transient Tradera failure, invalid taxonomy, and no comparables; photo validation is also covered, making 9/15 (60%). Key Vault failure, manual jobs disabled, photo upload failure, notification failure, and unexpected activity exception remain untested. This is below the required 80% threshold, independently requiring rejection.

## Test quality assessment

The new unit tests generally use injected fake HTTP clients, mocked Key Vault, and AsyncMock providers, and assert important request bodies, headers, URLs, response fields, retry counts, and ordering rather than status codes alone. Tests are mostly isolated with per-test fixtures and `monkeypatch`. However, the suite mixes legacy Vinted/Playwright route tests with the Tradera replacement, has incomplete API-route coverage against the design's current `/jobs` contract, and leaves several required retry, poison, notification, and integration behaviors untested. The reported security test is valuable and correctly catches the stale deployment declaration, but it is failing and therefore exposes an unresolved implementation defect.

## Gaps identified

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

## Implementation bugs

- `app/azure.yaml` still declares the removed `playwright_service`, violating the design's explicit removal requirement and causing `test_deployment_and_repository_security_contracts` to fail.

## Recommendation

Do not merge this PR. Remove the stale `playwright_service` service entry from `app/azure.yaml`, rerun the feature-owned deployment/security checks, and add the missing design-mandated error and operational coverage—especially disabled manual jobs, Key Vault failure, 429 behavior, photo-upload failure, notification isolation, and queue poison handling. The implementation bug alone makes the required QA decision REJECTED, and the documented error coverage is also below the 80% gate.
