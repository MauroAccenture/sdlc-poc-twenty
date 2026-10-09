"""Contract tests for the API and Playwright changes in this build."""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize("payload", [None, {"value": "not-a-list"}, {"value": [{}]}])
def test_graph_rejects_malformed_notifications(client, monkeypatch, payload):
    monkeypatch.setenv("GRAPH_CLIENT_STATE", "configured")
    response = client.post("/webhook/graph", headers={"clientState": "configured"}, json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_notification"


def test_graph_enqueues_root_folder_and_is_idempotent(client, monkeypatch):
    monkeypatch.setenv("GRAPH_CLIENT_STATE", "configured")
    body = {"value": [{"folder_path": "Vinted/Coat Size M"}]}
    headers = {"clientState": "configured"}
    first = client.post("/webhook/graph", headers=headers, json=body)
    second = client.post("/webhook/graph", headers=headers, json=body)
    assert first.status_code == second.status_code == 202
    assert first.json() == {"status": "accepted", "enqueued": 1}
    assert second.json() == {"status": "accepted", "enqueued": 0}
    assert len(client.app.state.queue.messages) == 1
    message = next(iter(client.app.state.queue.messages.values()))
    assert message["folder_path"] == "Vinted/Coat Size M"
    assert message["source"] == "graph"


def test_graph_ignores_folder_outside_configured_root(client, monkeypatch):
    monkeypatch.setenv("GRAPH_CLIENT_STATE", "configured")
    monkeypatch.setenv("GRAPH_ROOT_PATH", "Vinted")
    response = client.post("/webhook/graph", headers={"clientState": "configured"}, json={"value": [{"folder_path": "Other/Coat Size M"}]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_notification"


def test_manual_trigger_duplicate_is_accepted_without_second_message(client, monkeypatch):
    monkeypatch.setenv("MANUAL_TRIGGER_KEY", "manual")
    kwargs = {"headers": {"x-manual-trigger-key": "manual"}, "json": {"folder_path": "Vinted/Coat Size M"}}
    first = client.post("/pipeline/run", **kwargs)
    second = client.post("/pipeline/run", **kwargs)
    assert first.status_code == second.status_code == 202
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert first.json()["run_id"] == second.json()["run_id"]


def test_manual_trigger_maps_queue_failure_to_storage_error(client, monkeypatch):
    monkeypatch.setenv("MANUAL_TRIGGER_KEY", "manual")
    class BrokenQueue:
        async def send_once(self, value):
            raise RuntimeError("queue down")
    client.app.state.queue = BrokenQueue()
    response = client.post("/pipeline/run", headers={"x-manual-trigger-key": "manual"}, json={"folder_path": "Vinted/Coat Size M"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"


@pytest.mark.parametrize("path, expected_status, expected_code", [
    ("/draft", 401, "auth_required"),
])
def test_playwright_draft_requires_internal_auth(playwright_client, monkeypatch, path, expected_status, expected_code):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "service-token")
    response = playwright_client.post(path, json={"listing": {"title": "Coat", "description": "Good", "price": 10, "catalog_id": "tops", "size_id": "M", "photo_blobs": ["photo"]}, "photo_blobs": ["photo"]})
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


def _draft_payload():
    return {"listing": {"title": "Coat", "description": "Good", "price": 10, "catalog_id": "tops", "size_id": "M", "photo_blobs": ["photo"]}, "photo_blobs": ["photo"]}


def test_playwright_draft_maps_expired_session_to_documented_error(playwright_client, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "service-token")
    monkeypatch.setattr("playwright_service.main.session.status", AsyncMock(return_value="expired"))
    response = playwright_client.post("/draft", headers={"x-internal-service-token": "service-token"}, json=_draft_payload())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_expired"


@pytest.mark.parametrize("error_text, expected_code, expected_status", [("captcha_detected", "captcha_detected", 409), ("other failure", "playwright_unavailable", 503)])
def test_playwright_draft_maps_operational_failures(playwright_client, monkeypatch, error_text, expected_code, expected_status):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "service-token")
    monkeypatch.setattr("playwright_service.main.session.status", AsyncMock(return_value="authenticated"))
    monkeypatch.setattr("playwright_service.main.form.fill_draft", AsyncMock(side_effect=RuntimeError(error_text)))
    response = playwright_client.post("/draft", headers={"x-internal-service-token": "service-token"}, json=_draft_payload())
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
