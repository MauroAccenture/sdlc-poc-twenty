import os

def test_health_is_public_and_safe(client, monkeypatch):
    monkeypatch.setenv("GRAPH_SUBSCRIPTION_STATUS", "active")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["service"] == "api"

def test_graph_handshake_echoes_plain_text(client):
    response = client.post("/webhook/graph?validationToken=abc123")
    assert response.status_code == 200
    assert response.text == "abc123"
    assert response.headers["content-type"].startswith("text/plain")

def test_graph_rejects_wrong_client_state(client, monkeypatch):
    monkeypatch.setenv("GRAPH_CLIENT_STATE", "secret")
    response = client.post("/webhook/graph", headers={"clientState":"wrong"}, json={"value":[]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_client_state"

def test_graph_rejects_empty_notification(client, monkeypatch):
    monkeypatch.setenv("GRAPH_CLIENT_STATE", "secret")
    response = client.post("/webhook/graph", headers={"clientState":"secret"}, json={"value":[]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_notification"

def test_manual_trigger_requires_key(client, monkeypatch):
    monkeypatch.setenv("MANUAL_TRIGGER_KEY", "manual")
    response = client.post("/pipeline/run", json={"folder_path":"Vinted/Shirt Size M"})
    assert response.status_code == 401
    response = client.post("/pipeline/run", headers={"x-manual-trigger-key":"manual"}, json={"folder_path":"Vinted/Shirt Size M"})
    assert response.status_code == 202
    assert len(response.json()["run_id"]) == 32

def test_manual_trigger_rejects_unsafe_path(client, monkeypatch):
    monkeypatch.setenv("MANUAL_TRIGGER_KEY", "manual")
    response = client.post("/pipeline/run", headers={"x-manual-trigger-key":"manual"}, json={"folder_path":"../secret"})
    assert response.status_code == 422
