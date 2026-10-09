# Technical design: Switch marketplace to Tradera

## 1. Overview

Build the initial AutoLister application as a Python 3.12 service that receives Microsoft Graph/OneDrive file events, queues listing jobs through Azure Storage Queue, processes them in a worker, and creates an inactive fixed-price listing in Tradera. The seller is notified with a Tradera draft URL and activates the listing manually.

This is a direct Vinted-to-Tradera replacement, not a marketplace abstraction. The browser automation service is not part of the resulting system. The pipeline remains:

`OneDrive webhook/delta -> API -> Azure Storage Queue -> worker activities (download, analyse, research, generate, submit, notify)`.

The API and worker share Pydantic contracts. Tradera access is isolated in `worker/shared/tradera_client.py`, uses `httpx.AsyncClient`, obtains credentials from Azure Key Vault through `DefaultAzureCredential`, and never calls an activation/publish endpoint.

Assumptions made to make the greenfield implementation deterministic:
- The Tradera integration uses OAuth client credentials with application ID/key. The token request is `grant_type=client_credentials`; if the provisioned Tradera tenant requires an API-key header instead, only the client authentication method and configuration adapter change.
- Tradera REST endpoints are configurable, with production defaults documented in `worker/shared/tradera_client.py`; endpoint-specific JSON mapping is kept in that module because it is the only marketplace-specific boundary.
- Tradera accepts inactive fixed-price listings and returns a listing identifier and seller-facing draft URL.
- One listing job represents one source item and one or more source photos. Idempotency is based on the source file/item ID.
- No database is needed for the first version: queue messages carry the job and results are observable through structured logs and notifications. Duplicate delivery is handled by idempotent submission metadata.

## 2. Architecture changes

### Components

1. **`api` FastAPI container**
   - Exposes health, Graph webhook validation/notification, and a development/manual enqueue endpoint.
   - Uses Microsoft Graph delta processing to identify new image items, downloads metadata, and enqueues a `ListingJob`.
   - Does not call Tradera directly.

2. **Azure Storage Queue / Azurite**
   - Queue name: `listing-jobs`.
   - Messages contain a versioned JSON `ListingJob` with source item ID, drive ID, photo references, and correlation ID.
   - Visibility timeout and poison handling are configured in the worker.

3. **`worker` container**
   - Polls the queue and executes activities in fixed order: download photos, analyse product, research price, generate listing, submit draft, notify seller.
   - Uses `TraderaClient` directly; no internal HTTP submission service exists.
   - Retries transient activity errors and moves exhausted messages to `listing-jobs-poison`.

4. **Tradera client**
   - OAuth token acquisition/caching/refresh, create inactive listing, photo upload, and active/sold search.
   - All network calls use one `httpx.AsyncClient`, timeouts, and Tenacity retries for timeout, connection, 429, and 5xx responses. Do not retry non-transient 4xx responses.

5. **Azure Key Vault and Managed Identity**
   - Stores `tradera-app-id`, `tradera-app-key`, Graph credentials, and notification webhook URL.
   - Local development can use `KEYVAULT_URL` plus `DefaultAzureCredential` (Azure CLI/managed identity); no secrets are committed.

6. **Infrastructure/deployment**
   - Azure Container Apps for API and worker, Storage Account/queues, Key Vault, Log Analytics, and Container Apps environment.
   - No Playwright app, Playwright image, browser dependency, or internal service token.

### Security and operational rules

- Strip EXIF metadata before sending photos to external services.
- Validate webhook state and Graph signatures/validation token according to Microsoft Graph requirements.
- Redact access tokens, secrets, photo bytes, and personal data from logs.
- Use correlation IDs on every queue message and log event.
- The Tradera client exposes no `activate`, `publish`, or equivalent operation.

## 3. Files to create or modify

| File | Action | Purpose |
|---|---|---|
| `api/main.py` | Create | FastAPI application and lifespan initialization. |
| `api/routes/health.py` | Create | Liveness/readiness endpoints. |
| `api/routes/graph_webhook.py` | Create | Graph validation and notification handling. |
| `api/routes/jobs.py` | Create | Manual/local enqueue endpoint and idempotency validation. |
| `api/services/graph_service.py` | Create | Graph token, delta, metadata, and photo download operations. |
| `api/services/queue_service.py` | Create | Azure Queue message serialization and enqueueing. |
| `api/shared/models.py` | Create | Shared API/job Pydantic contracts. |
| `api/shared/config.py` | Create | API settings from environment. |
| `worker/main.py` | Create | Queue polling, activity orchestration, retry/dead-letter handling. |
| `worker/activities/download_photos.py` | Create | Download source photos and strip EXIF. |
| `worker/activities/analyse_product.py` | Create | GPT-4o analysis and Tradera taxonomy mapping. |
| `worker/activities/research_price.py` | Create | Comparable Tradera active/sold search and SEK recommendation. |
| `worker/activities/generate_listing.py` | Create | Generate/validate Tradera title, description, price, and payload. |
| `worker/activities/submit_draft.py` | Create | Create inactive Tradera listing and upload photos. |
| `worker/activities/notify_seller.py` | Create | Send draft URL notification. |
| `worker/shared/models.py` | Create | `ListingPayload`, `PriceResearch`, `DraftResult`, analysis, and job contracts. |
| `worker/shared/tradera_client.py` | Create | Authenticated async Tradera REST client with retry policy. |
| `worker/shared/tradera_taxonomy.py` | Create | Clothing categories, sizes, colors, and condition mappings. |
| `worker/shared/graph_client.py` | Create | Worker-side Graph photo access if required by the activity boundary. |
| `worker/shared/ai_client.py` | Create | GPT-4o analysis and listing generation wrapper. |
| `worker/shared/notification_client.py` | Create | Webhook notification wrapper. |
| `worker/shared/config.py` | Create | Worker settings and secret loading. |
| `tests/unit/test_tradera_client.py` | Create | Mocked auth, retry, create, upload, and search tests. |
| `tests/unit/test_tradera_taxonomy.py` | Create | Mapping and fallback coverage. |
| `tests/unit/test_research_price.py` | Create | Mocked search and SEK recommendation tests. |
| `tests/unit/test_listing_generator.py` | Create | Title, description, currency, and price validation. |
| `tests/unit/test_submit_draft.py` | Create | Direct client invocation and photo upload behavior. |
| `tests/unit/test_graph_webhook.py` | Create | Validation token and notification tests. |
| `tests/integration/test_queue_pipeline.py` | Create | Azurite queue enqueue/consume integration test. |
| `worker/requirements.txt` | Create | Python dependencies; includes httpx, tenacity, azure-identity, azure-keyvault-secrets, azure-storage-queue, pydantic, Pillow, OpenAI, FastAPI-compatible shared packages; excludes Playwright. |
| `api/requirements.txt` | Create | API dependencies. |
| `pyproject.toml` | Create | pytest, coverage, Ruff, mypy, and project configuration. |
| `Dockerfile.api` | Create | Minimal API image. |
| `Dockerfile.worker` | Create | Minimal worker image without browser packages. |
| `docker-compose.yml` | Create | Azurite, API, and worker only. |
| `infra/main.bicep` | Create | Azure resources, Container Apps, queue, Key Vault, identities, and secret references. |
| `infra/main.parameters.json` | Create | Non-secret deployment parameters; no Vinted/Playwright parameters. |
| `infra/modules/container_app.bicep` | Create | Reusable API/worker Container App module. |
| `infra/modules/key_vault.bicep` | Create | Key Vault and secret declarations/access policy or RBAC. |
| `azure.yaml` | Create | Azure Developer CLI service definitions for API and worker. |
| `DEPLOYMENT.md` | Create | Provisioning, Key Vault secret setup, local run, and smoke tests. |
| `CLAUDE.md` | Create | Project layout, Python conventions, and Tradera implementation constraints. |
| `.env.example` | Create | Variable names only with safe local defaults/placeholders. |
| `.gitignore` | Create | Python, secrets, build, and local Azure exclusions. |
| `README.md` | Create | Setup and pipeline overview. |

## 4. Implementation plan

1. Create the Python package layout and shared configuration. Use Pydantic Settings, strict validation, UTC timestamps, and a `schema_version` on queue messages.
2. Implement `ListingJob`, `ProductAnalysis`, `PriceResearch`, `ListingPayload`, and `DraftResult`. `ListingPayload` must contain `title`, `description`, `category_id`, `condition`, `size`, `color`, `price`, `currency="SEK"`, `fixed_price=True`, `inactive=True`, and photo references. Enforce title maximum 80 characters, non-empty description, and `price > 0`.
3. Implement `tradera_taxonomy.py` with explicit dictionaries for top-level clothing categories, sizes XS/S/M/L/XL/XXL and numeric shoe sizes 35–47, primary colors, and Tradera condition codes. Provide `map_category`, `map_size`, `map_color`, and `map_condition`; unknown values return a documented fallback or raise a taxonomy error rather than emitting an arbitrary ID.
4. Implement `TraderaClient` with constructor-injected HTTP transport for tests. Load app ID/key using `SecretClient(KEYVAULT_URL, DefaultAzureCredential())`. Cache token and expiry in memory; refresh at least 60 seconds before expiry. Use `Authorization: Bearer` and required Tradera application headers.
5. Add a retry decorator around each idempotent request and carefully bounded create-listing retry. Retry timeout/connection, 429 (honor `Retry-After`), and 500–599 with exponential backoff (maximum three attempts). Do not retry 400, 401, 403, or 404. Convert responses into typed models and raise `TraderaApiError` with redacted details.
6. Implement `create_listing` to send a fixed-price payload with `status/inactive` (the exact documented Tradera field name), never publish, and return `DraftResult(draft_id, tradera_draft_url)`. Persist the source correlation/idempotency key in the request if supported; otherwise check the job's durable idempotency marker before retrying creation.
7. Implement `upload_photo` as multipart upload with content type and size checks. Strip EXIF in `download_photos.py`, cap photo count/bytes, upload only after listing creation, and fail the activity if any required photo cannot be uploaded.
8. Implement `search_listings(query)` with category/brand/size/condition filters and separate active/sold status where supported. `research_price.py` removes outliers, uses sold prices preferentially, falls back to active prices, and returns a rounded SEK recommendation plus sample count. Empty results use the configured category-based fallback and mark the result as low confidence.
9. Implement analysis and listing generation. Map all model attributes through Tradera taxonomy, generate Swedish/English-safe content as configured, truncate titles to 80 characters without cutting surrogate characters, and always set currency SEK and fixed-price inactive semantics.
10. Implement `submit_draft.py` to call `TraderaClient.create_listing`, then `upload_photo` for each photo, and return the Tradera URL. No HTTP call to an internal Playwright service is permitted.
11. Implement API webhook/delta handling and queue serialization. Return Graph validation tokens as plain text, acknowledge valid notifications quickly with 202, and make repeated source IDs idempotent.
12. Implement queue worker polling, visibility timeout renewal for long jobs, activity-level structured logging, poison queue handling, and seller notification after successful submission only.
13. Add Bicep resources and identities. Grant API/worker identity Key Vault Secrets User and Storage Queue Data Contributor roles; inject only non-secret settings directly and use Key Vault for secrets.
14. Add Docker Compose with Azurite, API, and worker; provide health checks and local queue initialization. Confirm `GET /health` returns 200 and worker logs successful queue connection.
15. Add unit and Azurite integration tests, then run `pytest tests/unit/`, `pytest tests/integration/`, coverage checks, Ruff/mypy, and `az bicep build -f infra/main.bicep`.
16. Verify repository-wide absence of `playwright_service`, `ca-playwright`, `vinted_taxonomy`, `scraping_client`, Vinted credentials, and Playwright dependencies.

## 5. Data model changes

No relational schema or migration is required. The queue is the durable handoff; operational state is represented by message IDs/correlation IDs and notification records where the selected notification provider supports them.

### Queue message

```json
{
  "schema_version": 1,
  "job_id": "uuid",
  "source_item_id": "onedrive-item-id",
  "drive_id": "drive-id",
  "photo_refs": [{"item_id": "photo-id", "name": "front.jpg"}],
  "correlation_id": "uuid",
  "created_at": "2026-10-05T00:00:00Z"
}
```

### ListingPayload

```json
{
  "title": "Acne Studios wool coat",
  "description": "Good condition...",
  "category_id": "tradera-category-id",
  "condition": "USED_GOOD",
  "size": "M",
  "color": "BLACK",
  "price": 1295,
  "currency": "SEK",
  "fixed_price": true,
  "inactive": true
}
```

### PriceResearch

Fields: `recommended_price_sek: Decimal`, `sample_count: int`, `source: "sold"|"active"|"fallback"`, `confidence: "high"|"medium"|"low"`, and `comparables` containing redacted IDs/prices.

### DraftResult

Fields: `draft_id: str`, `tradera_draft_url: AnyHttpUrl`, `photo_count: int`, and `created_at: datetime`.

## 6. API design

### `GET /health`

No auth. Returns `200` when process is alive and dependencies are configured; readiness additionally checks Storage Queue connectivity.

```json
{"status":"ok","queue":"connected"}
```

### `POST /webhooks/graph`

Graph validation requests use `validationToken` and return the token as `text/plain` with 200. Notifications require the configured client state. Valid notifications are queued and acknowledged with 202.

Request:
```json
{"value":[{"resource":"drives/d/items/i","clientState":"configured-state"}]}
```
Response:
```json
{"accepted":1,"correlation_id":"uuid"}
```

### `POST /jobs`

Local/manual trigger; production access requires an internal deployment authentication mechanism and is disabled unless `ENABLE_MANUAL_JOBS=true`.

Request:
```json
{"source_item_id":"item-123","drive_id":"drive-123","photo_refs":[{"item_id":"photo-1","name":"front.jpg"}]}
```
Response `202`:
```json
{"job_id":"uuid","status":"queued"}
```

### Tradera outbound operations

These are not public application endpoints. `TraderaClient` calls the configured Tradera OAuth token endpoint (`POST TRADERA_TOKEN_URL`), listing endpoint (`POST TRADERA_LISTING_URL`), photo endpoint (`POST TRADERA_PHOTO_URL_TEMPLATE`), and search endpoint (`GET TRADERA_SEARCH_URL`). Exact request/response field names must follow the provisioned Tradera API version; the client normalizes them to the models above. Create requests must explicitly indicate fixed-price and inactive state.

## 7. Error handling

| Error | HTTP/status or queue behavior | Message |
|---|---|---|
| Missing/invalid Graph validation token | 400 | `Invalid validation request` |
| Invalid Graph client state/signature | 401 | `Unauthorized notification` |
| Malformed manual job | 422 | Pydantic validation detail |
| Manual jobs disabled | 404 | `Manual jobs are disabled` |
| Duplicate source item | 202 | `Job already queued` |
| Key Vault unavailable/secret missing | Worker retry, then poison | `Marketplace credentials unavailable` |
| Tradera 401 after refresh | Worker retry once, then poison | `Tradera authentication failed` |
| Tradera 400/403/404 | Worker poison; no retry | `Tradera request rejected` |
| Tradera timeout/connection/429/5xx | Exponential retry, then poison | `Tradera temporarily unavailable` |
| Invalid taxonomy mapping | Worker poison | `Unable to map product to Tradera taxonomy` |
| No price comparables | Continue with low-confidence fallback | `No comparable Tradera listings found` |
| Photo validation/EXIF failure | Worker poison | `Photo could not be prepared` |
| Photo upload failure | Worker retry, then poison | `Tradera photo upload failed` |
| Notification failure | Retry notification independently; do not recreate listing | `Draft created but seller notification failed` |
| Unexpected activity exception | Retry bounded times, then poison | `Listing job failed` |

API errors use `{"error":{"code":"...","message":"...","correlation_id":"..."}}`; secrets and upstream response bodies are never returned.

## 8. Testing strategy

- Verify `ListingPayload` rejects empty/overlong titles, non-positive prices, non-SEK currency, and non-fixed/inactive values.
- Verify every required clothing category, XS–XXL size, shoe size 35/47, primary color, and condition maps correctly; verify unknown mapping behavior.
- Mock Key Vault and `httpx` transport to test token acquisition, token cache, refresh-before-expiry, auth headers, and secret non-leakage.
- Mock 500, timeout, 429, 400, 401, and 404 responses to prove retry/non-retry behavior and bounded attempts.
- Test `create_listing` sends inactive fixed-price data and maps returned ID/URL; assert no publish call exists.
- Test multipart photo upload, invalid bytes, oversized files, and EXIF stripping.
- Mock active and sold Tradera search responses to test filters, sold-price preference, outlier removal, rounding, empty fallback, and SEK output.
- Test analysis and generation map Vinted-shaped extracted attributes only into Tradera fields and enforce title limit/currency.
- Test submit activity calls `create_listing` directly and uploads all photos; assert Playwright/internal HTTP client is absent.
- Test Graph validation token, invalid state, duplicate notifications, and 202 acknowledgement.
- Integration-test queue serialization, worker consumption, retry visibility, and poison behavior against Azurite.
- Run coverage with minimum 70% for `tradera_client.py` and `tradera_taxonomy.py`; run repository grep checks and Bicep compilation.

## 9. Environment variables

| Name | Description | Default |
|---|---|---|
| `KEYVAULT_URL` | Azure Key Vault URL | required in Azure; empty local |
| `AZURE_STORAGE_QUEUE_URL` | Queue endpoint | `http://azurite:10001/devstoreaccount1` |
| `AZURE_STORAGE_CONNECTION_STRING` | Local/Azurite connection string | Azurite default |
| `LISTING_QUEUE_NAME` | Work queue | `listing-jobs` |
| `TRADERA_API_BASE_URL` | Tradera API base URL | documented production URL |
| `TRADERA_TOKEN_URL` | OAuth token endpoint | `${TRADERA_API_BASE_URL}/oauth/token` |
| `TRADERA_LISTING_URL` | Create-listing endpoint | `${TRADERA_API_BASE_URL}/listings` |
| `TRADERA_PHOTO_URL_TEMPLATE` | Photo endpoint template | `${TRADERA_API_BASE_URL}/listings/{listing_id}/photos` |
| `TRADERA_SEARCH_URL` | Search endpoint | `${TRADERA_API_BASE_URL}/search/listings` |
| `TRADERA_TIMEOUT_SECONDS` | HTTP timeout | `30` |
| `TRADERA_MAX_RETRIES` | Transient retry count | `3` |
| `GRAPH_CLIENT_ID` | Key Vault secret name/reference | `graph-client-id` |
| `GRAPH_CLIENT_SECRET` | Key Vault secret name/reference | `graph-client-secret` |
| `GRAPH_CLIENT_STATE` | Webhook state secret reference | `graph-client-state` |
| `NOTIFICATION_WEBHOOK_URL` | Seller notification secret reference | `notification-webhook-url` |
| `OPENAI_MODEL` | Analysis/generation model | `gpt-4o` |
| `ENABLE_MANUAL_JOBS` | Enable local/manual enqueue route | `false` |
| `LOG_LEVEL` | Structured log level | `INFO` |

Key Vault secret names are exactly `tradera-app-id`, `tradera-app-key`, `graph-client-id`, `graph-client-secret`, `graph-client-state`, and `notification-webhook-url`. No Vinted secrets are provisioned.

## 10. Risks and mitigations

- **Tradera API version/field drift:** isolate URLs and JSON mapping in the client, use contract fixtures, and pin the documented API version/configuration.
- **OAuth behavior differs by account:** support token refresh and configurable auth mode while keeping credentials in Key Vault; fail closed if credentials are absent.
- **Create request duplicated after timeout:** use source correlation/idempotency key where supported and durable job deduplication before retry; never automatically publish.
- **Taxonomy IDs change:** keep mappings centralized, test all required values, and make unmapped values explicit failures.
- **External API throttling:** honor `Retry-After`, cap concurrency, and retry only transient responses.
- **Long photo processing exceeds queue visibility:** renew visibility during activity execution and configure a poison queue.
- **Sensitive image/credential leakage:** EXIF stripping, log redaction, managed identity, and no secrets in Compose/config.
- **Notification fails after successful draft:** retry notification independently and include draft ID in an operator-visible structured event to avoid duplicate listing creation.

## 11. Out of scope

- Auction listings, automatic activation/publishing, seller onboarding, or Vinted draft migration.
- Multi-marketplace abstractions, UI/mobile clients, or changes to OneDrive/Graph trigger semantics.
- Changes to the AI product-analysis capability beyond Tradera taxonomy mapping.
- Relational persistence, historical analytics, or a seller-facing dashboard.
- Live Tradera calls in automated tests.
- Browser automation, Playwright, Vinted credentials, Vinted taxonomy, third-party scraping, and Lobstr/Apify price research.
