import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("GRAPH_CLIENT_STATE", "MANUAL_TRIGGER_KEY", "INTERNAL_SERVICE_TOKEN", "GRAPH_SUBSCRIPTION_STATUS"):
        monkeypatch.delenv(key, raising=False)

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    queue = getattr(app.state, "queue", None)
    if hasattr(queue, "messages"):
        queue.messages.clear()
    return c
