# Run snapshot — 2026-10-05 05:57 UTC

## Current intent
# Switch marketplace to Tradera

# Replace Vinted with Tradera as the target marketplace

## What I want

Replace all Vinted-specific integration in the AutoLister application with [Tradera](https://www.tradera.com) — a Swedish second-hand marketplace that exposes a public REST API. The goal is to eliminate the fragile browser-automation layer entirely and drive listing creation through a stable, documented API instead.

### Summary of changes

The overall pipeline shape (OneDrive trigger → queue → worker activities → notify seller) stays the same. Only the marketplace-facing parts change:

| Area | Current (Vinted) | Target (Tradera) |
|---|---|---|
| Listing submission | `playwright_service/` browser automation via Playwright | Direct HTTP call to Tradera REST API from `worker` |
| Authentication | Cookie injection + session management | OAuth 2.0 / API key stored in Key Vault |
| Taxonomy | `worker/shared/vinted_taxonomy.py` | `worker/shared/tradera_taxonomy.py` |
| Price research | Lobstr.io / Apify scraper against Vinted | Tradera search API (comparable sold/active listings) |
| Currency | EUR | SEK |
| Draft concept | Vinted draft saved in browser; seller publishes manually | Tradera inactive listing (not yet published); seller activates manually |

### Detailed changes required

**Remove entirely:**
- `playwright_service/` directory (all files)
- `app/infra/modules/playwright_app.bicep`
- All references to `ca-playwright` Container App in `infra/main.bicep` and `azure.yaml`
- `worker/shared/vinted_taxonomy.py`
- `worker/shared/scraping_client.py` (replace with Tradera search, see below)

**Add:**
- `worker/shared/tradera_client.py` — async wrapper around the Tradera REST API covering: OAuth token fetch and refresh, create listing (inactive/draft state), upload photos, search active/sold listings for price research
- `worker/shared/tradera_taxonomy.py` — category ID, condition code, and size mappings for Tradera's catalogue
- `worker/activities/research_price.py` — rewrite to query Tradera's search API for comparable listings instead of a third-party scraper

**Modify:**
- `worker/activities/submit_draft.py` — replace the HTTP call to `playwright_service/draft` with a direct call to `tradera_client.create_listing()`
- `worker/activities/analyse_product.py` — map extracted attributes to Tradera taxonomy IDs instead of Vinted ones
- `worker/activities/generate_listing.py` — adjust title/description constraints and currency (SEK) to match Tradera listing requirements
- `worker/shared/models.py` — update `ListingPayload` fields and `DraftResult` to reflect Tradera's data model (e.g. replace `catalog_id` → `category_id`, `color_id` → `color`, currency `EUR` → `SEK`)
- `worker/main.py` — remove `_PlaywrightHttpClient`; submission is now a direct library call
- `infra/main.bicep` — remove playwright Container App and its dependencies; add `tradera-app-id` and `tradera-app-key` (or OAuth credentials) Key Vault secrets
- `infra/main.parameters.json` — remove playwright parameters
- `docker-compose.yml` — remove `playwright_service` service
- `azure.yaml` — remove `playwright_service` service entry
- `DEPLOYMENT.md` — update Key Vault secrets list and remove playwright-specific steps
- All `app/tests/` files that reference `playwright_service`, `vinted_taxonomy`, or `scraping_client`
- `AGENTS.md` — update stack hints, prompt guidelines, and file layout to reflect the new structure

**Key Vault secrets after change:**

| Secret name | Purpose |
|---|---|
| `tradera-app-id` | Tradera API application ID |
| `tradera-app-key` | Tradera API application key / OAuth secret |
| `graph-client-id` | Microsoft Graph (unchanged) |
| `graph-client-secret` | Microsoft Graph (unchanged) |
| `graph-client-state` | Webhook validation (unchanged) |
| `notification-webhook-url` | Seller notification (unchanged) |

Secrets to remove: `vinted-username`, `vinted-password`, `vinted-cookies`, `internal-service-token`.

---

## Acceptance criteria

### 1. playwright_service removed
- The `playwright_service/` directory no longer exists in the repository.
- `infra/modules/playwright_app.bicep` is deleted.
- No reference to `ca-playwright`, `playwright_service`, or `playwright` (as a runtime dependency) remains in `infra/main.bicep`, `azure.yaml`, or `docker-compose.yml`.
- `worker/requirements.txt` no longer lists `playwright` or `playwright-stealth`.

### 2. Tradera API client
- `worker/shared/tradera_client.py` exists and is importable.
- Implements at minimum: `authenticate()`, `create_listing(payload)` (creates in inactive/draft state), `upload_photo(listing_id, image_bytes)`, `search_listings(query)`.
- All HTTP calls use `httpx.AsyncClient` with `tenacity` retry on transient errors (5xx, timeout).
- Authentication uses `DefaultAzureCredential` to fetch `tradera-app-id` and `tradera-app-key` from Key Vault — no hardcoded credentials.

### 3. Taxonomy updated
- `worker/shared/tradera_taxonomy.py` exists with mappings covering at minimum: top-level clothing categories, common sizes (XS–XXL, numeric shoe sizes), primary colours, and condition codes as defined by Tradera's API.
- `worker/shared/vinted_taxonomy.py` is deleted.

### 4. Price research via Tradera search
- `worker/activities/research_price.py` queries Tradera's search API for comparable listings (same category, brand, size, condition).
- Returns a `PriceResearch` model with recommended price in SEK.
- Unit tests mock the Tradera API response — no live API call in tests.

### 5. Pipeline activities updated
- `submit_draft` calls `tradera_client.create_listing()` and returns a `DraftResult` with `draft_id` and `tradera_draft_url`.
- `analyse_product` maps to Tradera taxonomy IDs.
- `generate_listing` produces a `ListingPayload` with `currency: "SEK"` and a title within Tradera's character limit.
- Pipeline sequence (download → analyse → price → generate → submit → notify) is unchanged.

### 6. Models updated
- `ListingPayload` in `models.py` uses Tradera field names and `currency: str = "SEK"`.
- `DraftResult` includes a `tradera_draft_url` field.
- All Pydantic field validations (title length, price > 0) remain in place.

### 7. Infrastructure
- `az bicep build -f infra/main.bicep` completes with no errors.
- No playwright Container App or related resources in Bicep.
- `tradera-app-id` and `tradera-app-key` are declared as Key Vault secret references in Bicep.

### 8. Tests pass
- `pytest tests/unit/` passes with no failures — all Tradera API calls mocked.
- `pytest tests/integration/` passes against Azurite.
- No test file imports from `playwright_service`, `vinted_taxonomy`, or `scraping_client`.
- `test_listing_generator.py` asserts currency is SEK.
- Coverage for `worker/shared/tradera_client.py` and `worker/shared/tradera_taxonomy.py` is ≥ 70%.

### 9. Local development
- `docker compose up` starts all services without errors (Azurite, api, worker) — no playwright service.
- `GET http://localhost:8000/health` returns 200.
- Worker connects to Azurite queue on startup (log confirms connection).

### 10. Security
- No API keys, tokens, or credentials in committed code or config files.
- `tradera_client.py` fetches secrets from Key Vault via `DefaultAzureCredential`.
- EXIF stripping and all other security controls from the original design remain unchanged.

---

## Out of scope

- **Changing the trigger mechanism.** The OneDrive/Microsoft Graph webhook flow, Delta API, and queue-based pipeline entry point are unchanged.
- **Changing the AI layer.** GPT-4o photo analysis and listing description generation are unchanged; only the taxonomy mapping target changes.
- **Auction listings.** Only fixed-price (Buy Now) inactive listings are in scope. Auction mode is not required.
- **Automatic publishing.** The draft-only, human-published model is preserved. The Tradera client must never activate or publish a listing automatically.
- **Multi-marketplace support.** This is a direct replacement, not an abstraction layer. No adapter pattern or marketplace-agnostic interface is required.
- **UI or mobile app changes.** The seller interaction remains: receive a notification with a link, open the Tradera draft, review, and activate manually.
- **Seller account creation or Tradera onboarding.** A valid Tradera seller account and API credentials are assumed to exist before deployment.
- **Migrating existing Vinted drafts.** No data migration is required.

## Pipeline mode
**GREENFIELD** — Building a new application from scratch.
- There is no existing codebase. search_code() and recall() will return no results initially.
- Do NOT waste tool calls searching for existing patterns to match — there are none.
- Design and implement the COMPLETE initial project structure from zero.

## Codebase index
- Documents indexed: 5767
- Sources: local::/home/runner/work/sdlc-poc-frontend/sdlc-poc-frontend/app, local::/home/runner/work/sdlc-poc-frontend/sdlc-poc-frontend/memory, local::/home/runner/work/sdlc-poc-greenfield/sdlc-poc-greenfield/app, local::/home/runner/work/sdlc-poc-greenfield/sdlc-poc-greenfield/memory, local::/home/runner/work/sdlc-poc/sdlc-poc/app, local::C:\sdlc-poc-frontend\app
- Last updated: 2026-10-05T05:57:37.398518+00:00

## Tech stack
- Language: unknown
- Framework: unknown
- Test framework: unknown
- Database: unknown
- Auth: unknown

## Recent pipeline runs (last 3)
- [2026-10-01-create-first-version-of-new-application-] Create first version of new application item-lister

## Architecture decisions (summary)
# Architecture decisions


## 2026-10-01 — Create first version of new application item-lister
Designed the complete greenfield Vinted AutoLister architecture, including application services, Azure infrastructure, APIs, data contracts, deployment files, error handling, security, testing, and operational safeguards.


## Established patterns
- [[api-conventions]] — API conventions
- [[error-handling]] — Error handling
- [[rejection-patterns]] — Rejection patterns

## How to use this snapshot
Read this file first via read_file('context/run-snapshot.md').
Then use search_code() for specific questions about the codebase.
Use recall() for project decisions and patterns from previous runs.
Use read_file() when you need a specific file in full.