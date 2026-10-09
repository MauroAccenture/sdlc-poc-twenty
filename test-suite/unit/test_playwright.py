"""Updated marketplace-boundary tests; browser automation was removed by design."""
import pytest
from worker.shared.models import ListingPayload
from worker.shared.tradera_client import TraderaClient
class Vault:
    async def get_secret(self, name):
        class Secret: value = {"tradera-app-id": "app", "tradera-app-key": "key"}[name]
        return Secret()
@pytest.mark.asyncio
async def test_tradera_client_creates_inactive_fixed_price_draft():
    import httpx, json
    requests=[]
    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"access_token":"token","expires_in":3600}) if request.url.path.endswith("/oauth/token") else httpx.Response(201,json={"id":"d1","draftUrl":"https://tradera.test/d1"})
    client=TraderaClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), Vault())
    payload=ListingPayload(title="Shirt",description="Good",category_id="1001",condition="USED_GOOD",size="M",price=100,photo_refs=["photo.jpg"])
    result=await client.create_listing(payload)
    body=json.loads(requests[-1].content)
    assert result.draft_id=="d1" and body["status"]=="inactive" and body["listing_type"]=="fixed_price"
    await client._http.aclose()
@pytest.mark.asyncio
async def test_tradera_client_retries_transient_response(monkeypatch):
    import httpx
    calls=0
    async def handler(request):
        nonlocal calls; calls+=1
        if request.url.path.endswith("/oauth/token"): return httpx.Response(200,json={"access_token":"token","expires_in":3600})
        return httpx.Response(503) if calls==2 else httpx.Response(200,json=[])
    monkeypatch.setattr("worker.shared.tradera_client.asyncio.sleep", lambda _: _no_sleep())
    client=TraderaClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)),Vault())
    assert await client.search_listings({"status":"sold"})==[] and calls==3
    await client._http.aclose()
async def _no_sleep(): return None
