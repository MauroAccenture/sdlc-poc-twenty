"""Feature-owned Tradera contract and operational coverage."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
import pytest
from worker.activities.analyse_product import analyse_product
from worker.activities.notify_seller import notify_seller
from worker.activities.research_price import research_price
from worker.shared.models import DraftResult, PhotoBatch, PriceResearch, Product
from worker.shared.tradera_client import TraderaApiError, TraderaClient
from worker.shared.tradera_taxonomy import CATEGORIES, COLORS, CONDITIONS, SIZES
class Secret:
    def __init__(self,value): self.value=value
class KeyVault:
    def __init__(self): self.calls=[]
    async def get_secret(self,name): self.calls.append(name); return Secret({"tradera-app-id":"app-id","tradera-app-key":"app-key"}[name])
class Response:
    def __init__(self,status=200,body=None,headers=None): self.status_code=status; self._body=body or {}; self.headers=headers or {}
    def json(self): return self._body
class Http:
    def __init__(self,posts=None,requests=None): self.posts=list(posts or []); self.requests=list(requests or []); self.post_calls=[]; self.request_calls=[]
    async def post(self,*args,**kwargs): self.post_calls.append((args,kwargs)); return self.posts.pop(0)
    async def request(self,*args,**kwargs): self.request_calls.append((args,kwargs)); return self.requests.pop(0)
    async def aclose(self): pass
@pytest.mark.asyncio
async def test_expiring_token_is_refreshed_before_use():
    http=Http(posts=[Response(body={"access_token":"old","expires_in":3600}),Response(body={"access_token":"new","expires_in":3600})]); client=TraderaClient(http_client=http,keyvault=KeyVault()); assert await client.authenticate()=="old"; client._expires_at=datetime.now(timezone.utc)+timedelta(seconds=30); assert await client.authenticate()=="new"; assert len(http.post_calls)==2
@pytest.mark.asyncio
@pytest.mark.parametrize("exception",[httpx.TimeoutException("timeout"),httpx.ConnectError("connection")])
async def test_timeout_and_connection_errors_are_bounded_retries(monkeypatch,exception):
    async def no_sleep(_): pass
    monkeypatch.setattr("worker.shared.tradera_client.asyncio.sleep",no_sleep); http=Http(posts=[Response(body={"access_token":"t","expires_in":3600})]); http.request=AsyncMock(side_effect=exception); client=TraderaClient(http_client=http,keyvault=KeyVault())
    with pytest.raises(TraderaApiError,match="temporarily unavailable"): await client.search_listings({})
    assert http.request.await_count==client.max_retries
@pytest.mark.asyncio
@pytest.mark.parametrize("status",[403,404])
async def test_forbidden_and_missing_resources_are_not_retried(status):
    http=Http(posts=[Response(body={"access_token":"t","expires_in":3600})],requests=[Response(status)]); client=TraderaClient(http_client=http,keyvault=KeyVault())
    with pytest.raises(TraderaApiError,match="request rejected"): await client.search_listings({})
    assert len(http.request_calls)==1
@pytest.mark.asyncio
async def test_unauthorized_request_refreshes_token_once(monkeypatch):
    async def no_sleep(_): pass
    monkeypatch.setattr("worker.shared.tradera_client.asyncio.sleep",no_sleep); http=Http(posts=[Response(body={"access_token":"old","expires_in":3600}),Response(body={"access_token":"new","expires_in":3600})],requests=[Response(401),Response(body={"listings":[]})]); client=TraderaClient(http_client=http,keyvault=KeyVault()); assert await client.search_listings({})==[]; assert len(http.request_calls)==2; assert http.request_calls[1][1]["headers"]["Authorization"]=="Bearer new"
@pytest.mark.asyncio
async def test_photo_upload_sends_multipart_content_type_and_listing_url():
    http=Http(posts=[Response(body={"access_token":"t","expires_in":3600})],requests=[Response()]); client=TraderaClient(http_client=http,keyvault=KeyVault()); await client.upload_photo("draft-7",b"jpeg-bytes"); args,kwargs=http.request_calls[0]; assert args==("POST","https://api.tradera.com/listings/draft-7/photos"); assert kwargs["files"]["photo"][2]=="image/jpeg"; assert kwargs["files"]["photo"][1]==b"jpeg-bytes"
def test_all_required_taxonomy_values_have_explicit_mappings():
    assert {"tops","shirts","dresses","jackets","coats","trousers","jeans","skirts","shoes"}<=CATEGORIES.keys(); assert {"xs","s","m","l","xl","xxl"}<=SIZES.keys(); assert {str(n) for n in range(35,48)}<=SIZES.keys(); assert {"black","white","blue","red","green","yellow","brown","grey","beige","pink","purple","orange"}<=COLORS.keys(); assert {"new","very_good","good","satisfactory"}<=CONDITIONS.keys()
@pytest.mark.asyncio
async def test_analysis_maps_all_product_attributes_to_tradera_fields():
    ai=AsyncMock(); ai.analyse.return_value={"brand":"Acme","model":"Coat","category":"coats","size":"XL","condition":"very good","colour":"blue"}; result=await analyse_product(PhotoBatch(run_id="r1",folder_path="Acme Coat Size M",photo_blobs=["one.jpg"]),ai); assert result.category_id=="1005"; assert result.size=="XL"; assert result.condition=="VERY_GOOD"; assert result.color=="BLUE"
@pytest.mark.asyncio
async def test_price_research_falls_back_to_active_then_category_fallback():
    class Client:
        def __init__(self,active): self.active=active
        async def search_listings(self,query): return self.active if query["status"]=="active" else []
    product=Product(model="Coat",category="coats",condition="USED_GOOD",size="M",photo_blobs=["x"]); result=await research_price(product,Client([{"price":501},{"price":599}])); assert result.source=="active" and result.sample_count==2 and result.recommended_price_sek==Decimal("550"); empty=await research_price(product,Client([])); assert empty.source=="fallback" and empty.confidence.value=="low" and empty.recommended_price_sek>0
@pytest.mark.asyncio
async def test_notification_receives_draft_url_only_after_submission():
    notifier=AsyncMock(); notifier.send.return_value={"sent":True,"provider":"webhook","message_id":"m1"}; draft=DraftResult(draft_id="d1",tradera_draft_url="https://tradera.test/d1"); product=Product(model="Coat",category="coats",condition="USED_GOOD",size="M",photo_blobs=["x"]); pricing=PriceResearch(recommended_price_sek=100,sample_count=1,source="active",confidence="medium"); result=await notify_seller(product,pricing,draft,notifier); assert result.sent is True and "https://tradera.test/d1" in notifier.send.call_args.args[0]
def test_deployment_and_repository_security_contracts():
    root=Path(__file__).parents[2]; compose=(root/"docker-compose.yml").read_text(); azure=(root/"azure.yaml").read_text(); kv=(root/"infra/modules/keyvault.bicep").read_text(); requirements=(root/"worker/requirements.txt").read_text(); assert "playwright_service" not in compose and "playwright_service" not in azure; assert "tradera-app-id" in kv and "tradera-app-key" in kv; assert "playwright" not in requirements.lower()
