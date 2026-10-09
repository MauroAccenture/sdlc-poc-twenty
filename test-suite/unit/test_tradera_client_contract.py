import pytest
import httpx

from worker.shared.models import ListingPayload
from worker.shared.tradera_client import TraderaApiError, TraderaClient


class Secret:
    def __init__(self, value):
        self.value = value


class KeyVault:
    def __init__(self):
        self.calls = []

    async def get_secret(self, name):
        self.calls.append(name)
        return Secret({"tradera-app-id": "app-id", "tradera-app-key": "app-key"}[name])

    async def close(self):
        pass


class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}

    def json(self):
        return self._body


class Http:
    def __init__(self, posts=None, requests=None):
        self.posts = list(posts or [])
        self.requests = list(requests or [])
        self.post_calls = []
        self.request_calls = []

    async def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        return self.posts.pop(0)

    async def request(self, *args, **kwargs):
        self.request_calls.append((args, kwargs))
        return self.requests.pop(0)

    async def aclose(self):
        pass


@pytest.fixture
def listing():
    return ListingPayload(
        title="Wool coat", description="Good condition", category_id="1005",
        condition="USED_GOOD", size="M", color="BLACK", price=1200,
        photo_refs=["photo.jpg"], idempotency_key="source-1",
    )


@pytest.mark.asyncio
async def test_authentication_fetches_keyvault_credentials_and_caches_token():
    http = Http(posts=[Response(body={"access_token": "token", "expires_in": 3600})])
    vault = KeyVault()
    client = TraderaClient(http_client=http, keyvault=vault)

    assert await client.authenticate() == "token"
    assert await client.authenticate() == "token"
    assert len(http.post_calls) == 1
    assert vault.calls == ["tradera-app-id", "tradera-app-key"]
    assert http.post_calls[0][1]["auth"] == ("app-id", "app-key")


@pytest.mark.asyncio
async def test_create_listing_sends_inactive_fixed_price_and_idempotency_key(listing):
    http = Http(
        posts=[Response(body={"access_token": "token", "expires_in": 3600})],
        requests=[Response(body={"id": "draft-1", "draftUrl": "https://tradera.test/draft-1"})],
    )
    client = TraderaClient(http_client=http, keyvault=KeyVault())

    result = await client.create_listing(listing)

    assert result.draft_id == "draft-1"
    _, kwargs = http.request_calls[0]
    assert kwargs["json"]["inactive"] is True
    assert kwargs["json"]["fixed_price"] is True
    assert kwargs["json"]["status"] == "inactive"
    assert kwargs["json"]["listing_type"] == "fixed_price"
    assert kwargs["headers"]["Idempotency-Key"] == "source-1"
    assert "publish" not in kwargs["json"]


@pytest.mark.asyncio
async def test_transient_server_errors_are_retried_but_bad_request_is_not(monkeypatch):
    monkeypatch.setattr("worker.shared.tradera_client.asyncio.sleep", lambda _: _completed())

    async def completed():
        return None

    async def sleep_result():
        return None

    # Replace the helper with an awaitable without waiting for backoff.
    async def no_sleep(_):
        return None
    monkeypatch.setattr("worker.shared.tradera_client.asyncio.sleep", no_sleep)
    http = Http(
        posts=[Response(body={"access_token": "token", "expires_in": 3600})],
        requests=[Response(500), Response(500), Response(body={"listings": []})],
    )
    client = TraderaClient(http_client=http, keyvault=KeyVault())
    assert await client.search_listings({"status": "sold"}) == []
    assert len(http.request_calls) == 3

    bad_http = Http(
        posts=[Response(body={"access_token": "token", "expires_in": 3600})],
        requests=[Response(400)],
    )
    bad_client = TraderaClient(http_client=bad_http, keyvault=KeyVault())
    with pytest.raises(TraderaApiError, match="request rejected"):
        await bad_client.search_listings({"status": "sold"})
    assert len(bad_http.request_calls) == 1


@pytest.mark.asyncio
async def test_search_passes_filters_and_upload_rejects_empty_or_oversized_photo():
    http = Http(
        posts=[Response(body={"access_token": "token", "expires_in": 3600})],
        requests=[Response(body={"listings": [{"id": "1"}]})],
    )
    client = TraderaClient(http_client=http, keyvault=KeyVault())
    assert await client.search_listings({"category": "1005", "status": "sold"}) == [{"id": "1"}]
    assert http.request_calls[0][1]["params"] == {"category": "1005", "status": "sold"}

    with pytest.raises(ValueError):
        await client.upload_photo("draft-1", b"")
    with pytest.raises(ValueError):
        await client.upload_photo("draft-1", b"x" * (10 * 1024 * 1024 + 1))
