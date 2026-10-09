# Test results

## Summary
Passed: 31 | Failed: 14 | Skipped: 0 | Errors: 18 | Duration: 1.36s

## Test cases
| Test | Status | New/Updated/Existing | Description |
|---|---|---|---|
| test_expiring_token_is_refreshed_before_use | PASS | New | Refreshes OAuth token within the 60-second expiry window. |
| test_timeout_and_connection_errors_are_bounded_retries[exception0] | PASS | New | Bounds timeout retries. |
| test_timeout_and_connection_errors_are_bounded_retries[exception1] | PASS | New | Bounds connection-error retries. |
| test_forbidden_and_missing_resources_are_not_retried[403] | PASS | New | Rejects 403 without retry. |
| test_forbidden_and_missing_resources_are_not_retried[404] | PASS | New | Rejects 404 without retry. |
| test_unauthorized_request_refreshes_token_once | PASS | New | Refreshes credentials after a 401 response. |
| test_photo_upload_sends_multipart_content_type_and_listing_url | PASS | New | Verifies multipart photo upload contract. |
| test_all_required_taxonomy_values_have_explicit_mappings | PASS | New | Verifies all clothing, sizes, shoes, colors, and conditions. |
| test_analysis_maps_all_product_attributes_to_tradera_fields | PASS | New | Verifies analysis taxonomy mapping. |
| test_price_research_falls_back_to_active_then_category_fallback | PASS | New | Verifies active-price and empty-result fallback. |
| test_notification_receives_draft_url_only_after_submission | PASS | New | Verifies notification includes the created draft URL. |
| test_deployment_and_repository_security_contracts | FAIL | New | Detects the remaining playwright_service entry in azure.yaml. |
| test_pipeline_runs_in_order_and_checkpoints | PASS | Existing | Existing pipeline sequence test. |
| test_models_validate_positive_price_and_title | PASS | Existing | Existing model validation test. |
| test_listing_generation_uses_price_and_limits_title | PASS | Existing | Existing listing-generation test. |
| test_exif_stripper_keeps_pixels_and_removes_metadata | PASS | Existing | Existing EXIF security test. |
| test_tradera_client_creates_inactive_fixed_price_draft | PASS | Existing | Existing draft creation test. |
| test_tradera_client_retries_transient_response | PASS | Existing | Existing transient retry test. |
| test_authentication_fetches_keyvault_credentials_and_caches_token | PASS | Existing | Existing credential caching test. |
| test_create_listing_sends_inactive_fixed_price_and_idempotency_key | PASS | Existing | Existing inactive/idempotent listing test. |
| test_transient_server_errors_are_retried_but_bad_request_is_not | PASS | Existing | Existing 500/400 behavior test. |
| test_search_passes_filters_and_upload_rejects_empty_or_oversized_photo | PASS | Existing | Existing search and photo validation test. |
| test_required_taxonomy_maps_case_insensitively | PASS | Existing | Existing representative taxonomy test. |
| test_unknown_taxonomy_is_rejected | PASS | Existing | Existing unknown taxonomy test. |
| test_listing_contract_enforces_tradera_semantics | PASS | Existing | Existing SEK/inactive contract test. |
| test_price_research_prefers_sold_and_uses_sek_rounding | PASS | Existing | Existing sold-price research test. |
| test_generator_limits_title_and_sets_sek | PASS | Existing | Existing title/currency test. |
| test_submit_draft_creates_then_uploads_all_photos | PASS | Existing | Existing direct submission test. |
| test_price_research_filters_accessories_wrong_condition_outliers_and_uses_p75 | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| test_price_research_low_data_continues_with_insufficient_confidence[rows0] | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| test_price_research_low_data_continues_with_insufficient_confidence[rows1] | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| test_price_research_low_data_continues_with_insufficient_confidence[rows2] | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| test_keyvault_uses_managed_identity_and_caches_secret | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| test_queue_adapter_uses_azure_sdk_and_deduplicates_before_second_send | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| test_queue_sdk_failure_is_not_swallowed | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| test_renewer_creates_new_subscription_and_persists_result | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| test_renewer_alerts_and_reraises_sdk_failure | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| test_playwright_session_and_health_routes | FAIL | Existing | Legacy out-of-scope test; pre-existing under empty-baseline rule. |
| test_authenticated_publish_succeeds | FAIL | Existing | Legacy out-of-scope test; pre-existing under empty-baseline rule. |
| test_randomized_form_delays_are_all_within_one_to_four_seconds | FAIL | Existing | Legacy out-of-scope test; pre-existing under empty-baseline rule. |
| test_deployment_artifacts_declare_required_services_and_security | FAIL | Existing | Existing QA-gap test; pre-existing under empty-baseline rule. |
| API and legacy contract tests | ERROR | Existing | 18 existing tests cannot import the optional Dropbox dependency; pre-existing under empty-baseline rule. |

## Regression report
| Existing test | Result | Classification | Notes |
|---|---|---|---|
| Existing passing unit/integration tests listed above | PASSING | PASSING | Untouched and green. |
| Existing QA-gap and legacy tests listed above | FAIL/ERROR | PRE-EXISTING | No baseline file was present; these are outside the feature-owned test table and are excluded from regression count. |

## Coverage areas
Covered: OAuth caching and refresh, timeout/connection/401/403/404 handling, bounded retries, listing creation semantics, multipart photo success and validation, complete taxonomy sets, analysis mapping, sold/active/fallback price research, notification URL flow, title/SEK contracts, pipeline sequencing, EXIF stripping, and repository/deployment security checks.

Not covered: successful API route execution because the existing API import requires unavailable Dropbox; Azurite queue consumption/visibility/poison behavior; live Docker Compose startup; Bicep compilation; notification retry isolation; and Key Vault unavailable/poison orchestration. These were not counted as regressions.

## Implementation bugs found
- `app/azure.yaml` still declares the removed `playwright_service`, violating the design's explicit removal and deployment checks.

## Pre-existing failures
The full suite also reported 14 failures and 18 import/setup errors in existing API, legacy Playwright, and QA-gap tests. The baseline file was absent, so under the prescribed empty-baseline rule these are treated as pre-existing and do not affect the regression verdict.
