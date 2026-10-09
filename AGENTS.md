# AI Native SDLC — Agentic Pipeline Configuration

```yaml
# ══════════════════════════════════════════════════════════════════════════════
#  TWO DISTINCT REPOSITORIES
#
#  Pipeline repo  (this repo — MauroAccenture/sdlc-poc-frontend)
#    Contains: agents, orchestrator, context layer, SDLC tooling.
#    The workflow runs here. Agents, configs, and SDLC artifacts live here.
#    Never receives generated application code.
#
#  Target repo    (configured below — the app being developed)
#    Contains: the application source code agents read, modify, and test.
#    Cloned into ./app at pipeline startup. Changes made by agents stay
#    in that local clone and are never committed back to the target repo
#    or to this pipeline repo.
# ══════════════════════════════════════════════════════════════════════════════

# ─── Target application repository ────────────────────────────────────────────
# The orchestrator clones this into local_path before agents run, and
# re-syncs it on subsequent runs (fetch + reset --hard, no full re-clone).
# The local clone is automatically indexed for RAG — do NOT add it to sources.
# Requires: GITHUB_TOKEN with read access to the target repo.
target_repo:
  repo:       twentyhq/twenty
  branch:     main
  local_path: ./app
  index:
    include_extensions: [.ts, .html, .scss, .css, .yaml, .yml, .json, .md, .tsx]
    exclude_dirs: [.git, node_modules, dist, .angular]

# ─── Additional reference sources ─────────────────────────────────────────────
# Indexed alongside the target repo for extra RAG context (docs, templates…).
# Do NOT add the target repo's local_path here — it is indexed automatically.
# Supported types: github, local
# Planned types:   sharepoint, confluence, notion (stubs available)
sources:
  # Pipeline repo docs — design guides, SDLC templates, architecture notes.
  - type: github
    repo: MauroAccenture/sdlc-poc-twenty
    branch: main
    include_paths:
      - docs/
    include_extensions:
      - .md
    max_file_size_kb: 100

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
    # Switch provider here; rebuild index after switching (dimension change)
    embedder:
      provider: sentence_transformers   # free, local, no API key needed
      model: all-MiniLM-L6-v2          # 384 dims, fast, good quality
      batch_size: 64
      cache_dir: .cache/embeddings

      # Azure OpenAI alternative (stub — not yet implemented):
      # provider: azure_openai
      # deployment: text-embedding-ada-002
      # api_version: "2024-02-01"
      # Requires: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY secrets

    # Vector store — where embeddings are persisted
    store:
      provider: qdrant                  # remote Qdrant Cloud cluster
      collection_name: sdlc-codebase
      vector_size: 384                  # must match embedder dimension
      # Requires: QDRANT_URL, QDRANT_API_KEY secrets

      # Local Chroma fallback (zero infrastructure, no secrets needed):
      # provider: chroma
      # persist_directory: .index/chroma
      # collection_name: sdlc-codebase

      # Azure AI Search alternative (stub — not yet implemented):
      # provider: azure_search
      # index_name: sdlc-codebase
      # use_hybrid_search: true
      # Requires: AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_API_KEY secrets

  
# ─── Tech stack hints ─────────────────────────────────────────────
stack:
  language:       typescript
  runtime:        node24
  package_manager: yarn4
  monorepo:       nx
  framework:      react
  frontend:       react-vite-jotai-linaria
  backend:        nestjs-graphql-typeorm
  database:       postgresql
  cache:          redis
  test_framework: jest-vitest-playwright
  auth:           twenty-native
  style:          graphql
  deployment:     docker_compose
  container:      docker
  iac:            none

# ─── Deployment configuration ─────────────────────────────────────
deployment:
  enabled: false                  # Enable only when deployment is needed
  tool: docker_compose
  target: docker
  artifacts:
    - packages/twenty-docker/docker-compose.yml
    - packages/twenty-docker/docker-compose.dev.yml

  deploy_commands: |
    cd packages/twenty-docker
    docker compose up -d

# ─── Repository configuration ─────────────────────────────────────
repository:
  url: https://github.com/twentyhq/twenty
  instructions:
    - CLAUDE.md

  packages:
    frontend: packages/twenty-front
    backend: packages/twenty-server
    ui: packages/twenty-ui
    shared: packages/twenty-shared
    e2e: packages/twenty-e2e-testing

  discovery:
    read_repository_instructions: true
    inspect_existing_patterns: true
    prefer_existing_dependencies: true

# ─── Build and validation ─────────────────────────────────────────
validation:
  commands:
    - yarn install --immutable
    - npx nx build twenty-shared
    - npx nx test twenty-front
    - npx nx test twenty-server

  guidelines:
    - Use existing Nx targets for affected packages
    - Run targeted tests before full package tests
    - Follow package-specific testing conventions
    - Do not introduce new deployment infrastructure
    - Do not change unrelated packages

  # ─── LLM provider ───────────────────────────────────────────────────────────
  # Switch LLM here without touching agent code.  Env vars LLM_PROVIDER and
  # LLM_MODEL (if set) take precedence over these values at runtime.
  llm:
    provider: anthropic          # anthropic | openai
    model: claude-haiku-4-5      # default for all agents; override per-agent below
    # OpenAI alternative:
    # provider: openai
    # model: gpt-4o-mini         # or gpt-4o, o1-mini, etc.
    # Requires: OPENAI_API_KEY   (or AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY
    #                             for Azure-hosted OpenAI models)

    # Per-agent overrides — omit an entry to inherit the global provider/model above.
    # Each entry accepts "provider" and/or "model" independently.
    agents:
      designer:  
        provider: openai
        model: gpt-5.6-luna           # independent second opinion from a different provider
      code_reviewer:
        provider: openai
        model: gpt-5.6-luna           # independent second opinion from a different provider
      coder:     
        provider: openai
        model: gpt-5.6-luna           # independent second opinion from a different provider
      tester:
        provider: openai
        model: gpt-5.6-luna
      qa_engineer:
        provider: openai
        model: gpt-5.6-luna 
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
