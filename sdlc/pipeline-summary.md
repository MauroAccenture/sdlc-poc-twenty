## 🤖 AI-generated feature: Switch marketplace to Tradera

### Design
Designed the complete greenfield Tradera-based AutoLister architecture, including the direct API client, taxonomy, worker pipeline, infrastructure, contracts, security, error handling, and comprehensive testing strategy.

### Implementation
Implemented the missing `QueueMessage` compatibility contract, preserving Tradera models and enabling the pipeline and Tradera feature tests to pass.

### Code review
✅ approved

### Tests
A = 0  
B = 1  
The expanded Tradera tests pass, but the full suite exposes a remaining implementation bug: `azure.yaml` still declares `playwright_service`.  
REJECTED

### QA
❌ REJECTED

---
See `sdlc/` for full agent reports.
