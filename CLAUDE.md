# Claude AI-SDLC — Agentic Pipeline Configuration

```yaml
# ══════════════════════════════════════════════════════════════════════════════
#  GREENFIELD PROJECT — Vinted AutoLister
#
#  Pipeline repo  (this repo — MauroAccenture/sdlc-poc-greenfield)
#    Contains: agents, orchestrator, context layer, SDLC tooling.
#    The workflow runs here. Agents, configs, and SDLC artifacts live here.
#
#  Target app     (built from scratch into ./app — no upstream repo to clone)
#    Python 3.12 multi-service application on Azure Container Apps.
#    The Coder scaffolds the entire project; nothing is cloned.
# ══════════════════════════════════════════════════════════════════════════════

# ─── Target application directory ─────────────────────────────────────────────
# No repo to clone in greenfield mode. local_path and index tell the pipeline
# where the Coder will write files and what to index once code exists.
target_repo:
  local_path: ./app
  index:
    include_extensions: [.py, .yaml, .yml, .json, .md, .toml, .txt, .bicep]
    exclude_dirs: [.git, __pycache__, .venv, venv, dist, .mypy_cache, .pytest_cache, node_modules]

# ─── Additional reference sources ─────────────────────────────────────────────
sources:
  # Local memory vault — accumulated project knowledge for recall()
  - type: local
    path: memory
    include_extensions: [.md]
    exclude_dirs: [patterns/graph]

  # SharePoint — not yet implemented, stub available
  # - type: sharepoint
  #   site_url: https://myorg.sharepoint.com/sites/Engineering
  #   library: Documents
  #   folder: /Architecture

pipeline:

  # ─── Pipeline mode ──────────────────────────────────────────────────────────
  # 'greenfield' — build a new application from scratch (no repo to clone).
  # 'brownfield' (or omit) — extend an existing repo configured in target_repo.
  mode: greenfield

  # ─── Human review gates ─────────────────────────────────────────────────────
  stages:
    after_design:
      human_review: false

    after_code_review:
      human_review: false

    after_qa_review:
      human_review: false

  # ─── Context layer ──────────────────────────────────────────────────────────
  context:

    # Embedder — converts text to vectors
    embedder:
      provider: sentence_transformers   # free, local, no API key needed
      model: all-MiniLM-L6-v2          # 384 dims, fast, good quality
      batch_size: 64
      cache_dir: .cache/embeddings

    # Vector store — where embeddings are persisted
    store:
      provider: qdrant                  # remote Qdrant Cloud cluster
      collection_name: sdlc-codebase
      vector_size: 384                  # must match embedder dimension
      # Requires: QDRANT_URL, QDRANT_API_KEY secrets

      # Local Chroma fallback (zero infrastructure):
      # provider: chroma
      # persist_directory: .index/chroma
      # collection_name: sdlc-codebase

  # ─── Tech stack hints ───────────────────────────────────────────────────────
    stack:
    language:       python
    runtime:        python3.12
    framework:      fastapi
    test_framework: pytest
    auth:           managed_identity    # DefaultAzureCredential everywhere; no explicit keys
    style:          async               # asyncio + tenacity throughout
    deployment:     azure_container_apps
    container:      docker
    iac:            bicep

  # ─── Deployment configuration ───────────────────────────────────────────────
  deployment:
    tool:    azd                     # Azure Developer CLI
    target:  azure_container_apps
    region:  swedencentral
    artifacts:
      - api/Dockerfile
      - worker/Dockerfile
      - playwright_service/Dockerfile
      - renewer/Dockerfile
      - infra/main.bicep
      - infra/main.parameters.json
      - azure.yaml
    deploy_commands: |
      azd up
      azd deploy

  # ─── LLM provider ───────────────────────────────────────────────────────────
  llm:
    provider: anthropic          # anthropic | openai
    model: claude-haiku-4-5      # default for all agents; override per-agent below

    agents:
      designer:
        provider: openai
        model: gpt-5.6-luna
      code_reviewer:
        provider: openai
        model: gpt-5.6-luna
      coder:
        provider: openai
        model: gpt-5.6-luna
      tester:
        provider: openai
        model: gpt-5.6-luna
      qa_engineer:
        provider: openai
        model: gpt-5.6-luna

  # ─── Per-agent prompt guidelines ────────────────────────────────────────────
    prompts:

    design_guidelines: |
      This is a Python 3.12 greenfield application — Vinted AutoLister — built
      as four independent FastAPI/async services on Azure Container Apps:
        api/               — Webhook receiver (FastAPI, external ingress, port 8000)
        worker/            — Pipeline orchestrator (async Python, internal ingress)
        playwright_service/ — Browser automation (FastAPI, internal ingress, port 8002)
        renewer/           — Graph subscription renewal (cron job, exits after run)
      Plus: infra/ (Bicep modules), tests/unit/, tests/integration/, docker-compose.yml

      Design conventions:
      - All Azure SDK access uses DefaultAzureCredential (Managed Identity) — NEVER
        explicit keys, connection strings, or env-var secrets
      - All inter-service data contracts are Pydantic v2 models in worker/shared/models.py
      - Worker pipeline activities are async functions with @retry (tenacity) decorators
      - Secrets from external providers (scraping API key, Graph client secret) live in
        Key Vault and are fetched at cold start then cached in memory
      - Each app has its own Dockerfile and requirements.txt
      - docker-compose.yml wires all four apps plus Azurite for local development

      Every design doc MUST include a "Files to create or modify" section listing
      every file across all app directories, with its full path from the repo root.

    build_guidelines: |
      Python 3.12 multi-service application. Conventions:

      File layout (all paths relative to ./app/):
        api/main.py, api/webhook_handler.py, api/manual_trigger.py
        api/Dockerfile, api/requirements.txt
        worker/main.py, worker/pipeline.py
        worker/activities/{download_photos,analyse_product,research_price,
                           generate_listing,submit_draft,notify_seller}.py
        worker/shared/{graph_client,keyvault_client,blob_client,queue_client,
                       openai_client,scraping_client,vinted_taxonomy,
                       exif_stripper,checkpoint,models}.py
        worker/Dockerfile, worker/requirements.txt
        playwright_service/main.py, playwright_service/vinted_form.py
        playwright_service/session_manager.py, playwright_service/rate_limiter.py
        playwright_service/Dockerfile, playwright_service/requirements.txt
        renewer/main.py, renewer/Dockerfile, renewer/requirements.txt
        infra/main.bicep, infra/main.parameters.json
        infra/modules/{container_apps_environment,api_app,worker_app,
                       playwright_app,renewer_job,storage,keyvault,
                       managed_identity}.bicep
        docker-compose.yml, azure.yaml, .gitignore

      Code conventions:
      - async def for all route handlers and activity functions
      - Pydantic v2 models for all request/response and inter-activity types
      - All Azure SDK clients: DefaultAzureCredential, never explicit keys
      - @retry (tenacity) on every activity function with exponential backoff
      - Dockerfiles: python:3.12-slim base, non-root user, pip install --no-cache-dir -r requirements.txt
      - docker-compose.yml: all four apps + azurite service (mcr.microsoft.com/azure-storage/azurite)
      - EXIF stripping (Pillow) applied to every photo before any blob upload
      - /publish route on playwright_service requires {"confirm": true} in body
      - rate_limiter.py: 2 req/s cap, randomised 1–4 s per-field delay (not a constant)
      - No hardcoded secrets, tokens, or connection strings anywhere

    test_guidelines: |
      Tests use pytest. All in ./app/tests/ (relative to repo root: app/tests/).

      File layout:
        tests/unit/test_folder_parser.py, test_exif_stripper.py,
                   test_listing_generator.py, test_price_researcher.py,
                   test_vinted_taxonomy.py, test_checkpoint.py, test_api.py,
                   test_rate_limiter.py
        tests/integration/test_pipeline.py, test_playwright_service.py
        tests/conftest.py

      Conventions:
      - All Azure SDK calls mocked via unittest.mock or pytest-mock in unit tests
      - FastAPI routes tested with httpx.AsyncClient(app=app, base_url="http://test")
      - Integration tests use Azurite (Azure Storage emulator) — no real Azure calls
      - test_exif_stripper.py: create a real JPEG with embedded GPS EXIF, strip it,
        assert GPS tags absent from the result
      - test_folder_parser.py: cover standard format, multi-word brand, size variations
        (S/M/L/XL, numeric, W32 L30), missing size
      - All modules in worker/activities/ and worker/shared/ must have ≥ 70% coverage
      Install and run:
        run_command("pip install pytest pytest-asyncio pytest-mock httpx pillow -q", "app")
        run_command("python -m pytest tests/unit/ -v 2>&1", "app")

    code_review_guidelines: |
      Verify: DefaultAzureCredential used in every Azure SDK instantiation (grep for
      explicit keys or connection strings — any found = REJECTED), all Pydantic models
      have field validation (title ≤ 60 chars, price > 0), @retry decorator on every
      activity function, EXIF stripping called before any blob upload in
      download_photos.py, /publish requires confirm=True (missing = 422), rate_limiter
      enforces 2 req/s cap and delays are randomised (1–4 s, not a fixed constant),
      all Dockerfiles use python:3.12-slim and drop to a non-root user, no secrets
      in committed files.

    qa_guidelines: |
      Confirm: all Azure SDK calls are mocked in unit tests (no real Azure calls),
      integration tests use Azurite (not real Azure Storage), test_exif_stripper.py
      tests with a real EXIF-embedded JPEG and asserts GPS tags absent after stripping,
      activity sequence in pipeline.py matches design (download→analyse→price→generate→
      draft→notify), checkpoint.py is called before and after each activity, /publish
      returns 422 when confirm is missing or false, rate_limiter delay is verifiably
      randomised in tests, no test requires a live Vinted or Azure connection.
```

---

## Agent reference

| Agent | File | New tools |
|-------|------|-----------|
| Designer | `agents/designer.py` | `search_code`, `recall` |
| Coder | `agents/coder.py` | `search_code`, `recall` |
| Code reviewer | `agents/code_reviewer.py` | `search_code` |
| Tester | `agents/tester.py` | `search_code` |
| QA engineer | `agents/qa_engineer.py` | — |

## Context layer reference

| Component | File | Swap by changing |
|-----------|------|-----------------|
| Source connector | `context/connectors/` | `sources[].type` |
| Embedder | `context/embedders/` | `context.embedder.provider` |
| Vector store | `context/stores/` | `context.store.provider` |

## Adding Qdrant Cloud

1. Create a free cluster at [cloud.qdrant.io](https://cloud.qdrant.io).
2. Copy the cluster URL and API key.
3. Set `QDRANT_URL` and `QDRANT_API_KEY` as secrets (env vars or `.env`).
4. Ensure `provider: qdrant` is active in the `store:` block above.
5. Run with `FORCE_FULL_REINDEX=true` to build the initial remote index.

## Adding SharePoint

1. Set `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_TENANT_ID` as secrets.
2. Implement `context/connectors/sharepoint_connector.py` (see stub + docstring).
3. Uncomment the SharePoint source block above.
4. Run with `FORCE_FULL_REINDEX=true` to build the initial index.
