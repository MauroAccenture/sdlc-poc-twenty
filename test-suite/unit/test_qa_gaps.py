"""Additional acceptance and risk-focused coverage for the architecture contracts."""
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from worker.shared.models import Condition, Product


def product():
    return Product(model="Jean Shirt", category="tops", condition=Condition.GOOD,
                   size="M", catalog_id="tops", size_id="M", photo_blobs=["p.jpg"], brand="Dressmann")


def test_documented_graph_payload_is_accepted_and_enqueues_exact_message(client, monkeypatch):
    monkeypatch.setenv("GRAPH_CLIENT_STATE", "configured-secret")
    response = client.post("/webhook/graph", headers={"clientState": "configured-secret"}, json={
        "value": [{"subscriptionId": "sub-1", "clientState": "not-in-body",
                   "resource": "drives/me/items/root"}]
    })
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "enqueued": 1}
    assert len(client.app.state.queue.messages) == 1
    message = next(iter(client.app.state.queue.messages.values()))
    assert message["folder_path"] == "Vinted"
    assert message["source"] == "graph"


@pytest.mark.asyncio
async def test_price_research_filters_accessories_wrong_condition_outliers_and_uses_p75():
    from worker.activities.research_price import research_price
    rows = [
        {"title": "a", "price": 10, "condition": "good", "category": "tops"},
        {"title": "b", "price": 12, "condition": "good", "category": "tops"},
        {"title": "c", "price": 14, "condition": "good", "category": "tops"},
        {"title": "d", "price": 16, "condition": "good", "category": "tops"},
        {"title": "accessory", "price": 999, "condition": "good", "category": "accessories"},
        {"title": "wrong", "price": 11, "condition": "new", "category": "tops"},
        {"title": "bad", "price": 0, "condition": "good", "category": "tops"},
    ]
    provider = MagicMock(search=AsyncMock(return_value=rows))
    result = await research_price(product(), provider)
    assert [x.price for x in result.comparables] == [Decimal("10"), Decimal("12"), Decimal("14"), Decimal("16")]
    assert result.confidence == "high"
    assert result.recommended_price == result.upper_percentile


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [{"title": "a", "price": 10, "condition": "good", "category": "tops"}],
                                    [{"title": "a", "price": 10, "condition": "good", "category": "tops"},
                                     {"title": "b", "price": 12, "condition": "good", "category": "tops"}]])
async def test_price_research_low_data_continues_with_insufficient_confidence(rows):
    from worker.activities.research_price import research_price
    if not rows:
        with pytest.raises(ValueError):
            await research_price(product(), MagicMock(search=AsyncMock(return_value=rows)))
    else:
        result = await research_price(product(), MagicMock(search=AsyncMock(return_value=rows)))
        assert result.confidence == "insufficient_data"
        assert result.recommended_price == result.median


@pytest.mark.asyncio
async def test_keyvault_uses_managed_identity_and_caches_secret():
    import worker.shared.keyvault_client as module
    credential = MagicMock()
    secret_client = MagicMock()
    secret_client.get_secret = AsyncMock(return_value=MagicMock(value="top-secret"))
    secret_client.close = AsyncMock()
    with patch.object(module, "DefaultAzureCredential", return_value=credential) as cred, \
         patch.object(module, "SecretClient", return_value=secret_client):
        client = module.KeyVaultClient("https://vault.example")
        assert await client.get("api-key") == "top-secret"
        assert await client.get("api-key") == "top-secret"
        secret_client.get_secret.assert_awaited_once_with("api-key")
        assert "top-secret" not in repr(client.__dict__)
        await client.close()
        cred.assert_called_once_with()


@pytest.mark.asyncio
async def test_queue_adapter_uses_azure_sdk_and_deduplicates_before_second_send():
    import worker.shared.queue_client as module
    queue = MagicMock()
    queue.send_message = AsyncMock(return_value=MagicMock(id="m1"))
    queue.close = AsyncMock()
    credential = MagicMock()
    credential.close = AsyncMock()
    with patch.object(module, "DefaultAzureCredential", return_value=credential), \
         patch.object(module, "QueueClient", return_value=queue):
        store = module.QueueStore("https://storage.example", "listing-requests")
        value = {"run_id": "stable-run", "folder_path": "Vinted/A"}
        assert await store.send_once(value) is True
        assert await store.send_once(value) is False
        queue.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_queue_sdk_failure_is_not_swallowed():
    import worker.shared.queue_client as module
    queue = MagicMock()
    queue.send_message = AsyncMock(side_effect=RuntimeError("azure unavailable"))
    with patch.object(module, "DefaultAzureCredential", return_value=MagicMock()), \
         patch.object(module, "QueueClient", return_value=queue):
        store = module.QueueStore("https://storage.example", "q")
        with pytest.raises(RuntimeError, match="azure unavailable"):
            await store.send_once({"run_id": "r"})


@pytest.mark.asyncio
async def test_renewer_creates_new_subscription_and_persists_result(monkeypatch):
    from renewer.main import renew
    monkeypatch.setenv("GRAPH_NOTIFICATION_URL", "https://api.example/webhook/graph")
    state = MagicMock(read=AsyncMock(return_value=None), write=AsyncMock())
    graph = MagicMock(create_subscription=AsyncMock(return_value={"id": "new", "expiration": "later"}))
    notifier = MagicMock(send=AsyncMock())
    await renew(graph, state, notifier)
    graph.create_subscription.assert_awaited_once_with("https://api.example/webhook/graph")
    state.write.assert_awaited_once_with({"id": "new", "expiration": "later"})
    notifier.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_renewer_alerts_and_reraises_sdk_failure():
    from renewer.main import renew
    state = MagicMock(read=AsyncMock(return_value={"id": "old"}))
    graph = MagicMock(renew_subscription=AsyncMock(side_effect=RuntimeError("secret-token must not leak")))
    notifier = MagicMock(send=AsyncMock())
    with pytest.raises(RuntimeError):
        await renew(graph, state, notifier)
    notifier.send.assert_awaited_once()
    assert "secret-token" not in notifier.send.call_args.args[0]


def test_playwright_session_and_health_routes(monkeypatch, tmp_path):
    from playwright_service.main import app, session
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "service-token")
    session.profile_path = tmp_path / "profile"
    with TestClient(app) as c:
        assert c.get("/health").json() == {"status": "healthy", "service": "playwright"}
        assert c.get("/session").json() == {"status": "expired"}


def test_authenticated_publish_succeeds(monkeypatch):
    from playwright_service.main import app, session
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "service-token")
    session.profile_path = Path("/tmp/vinted-test-profile")
    with TestClient(app) as c:
        response = c.post("/publish", headers={"x-internal-service-token": "service-token"},
                          json={"draft_id": "vnt-1", "confirm": True})
        assert response.status_code == 200
        assert response.json() == {"draft_id": "vnt-1", "status": "published"}


@pytest.mark.asyncio
async def test_randomized_form_delays_are_all_within_one_to_four_seconds(monkeypatch):
    from playwright_service.vinted_form import VintedForm
    from worker.shared.models import ListingPayload
    values = []
    async def fake_sleep(value):
        values.append(value)
    monkeypatch.setattr("playwright_service.vinted_form.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("playwright_service.vinted_form.random.uniform", lambda a, b: (a + b) / 2)
    await VintedForm().fill_draft(ListingPayload(title="T", description="D", price=Decimal("1"),
        catalog_id="tops", size_id="M", photo_blobs=["p"]))
    assert len(values) == 5 and all(1 <= value <= 4 for value in values)


def test_deployment_artifacts_declare_required_services_and_security():
    root = Path(__file__).parents[2]
    compose = (root / "docker-compose.yml").read_text()
    assert all(name in compose for name in ("azurite", "api", "worker", "playwright_service", "renewer"))
    bicep = (root / "infra/main.bicep").read_text()
    assert all(term in bicep for term in ("managedEnvironments", "storageAccounts", "KeyVault", "CognitiveServices", "ca-api", "ca-worker", "ca-playwright"))
    assert "clientSecret" not in bicep and "apiKey" not in bicep
