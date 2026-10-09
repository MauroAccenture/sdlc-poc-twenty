import pytest
from decimal import Decimal
from worker.shared.models import ListingPayload, Product, PriceResearch
from worker.shared.tradera_taxonomy import map_category, map_size, map_color, map_condition
from worker.activities.research_price import research_price
from worker.activities.generate_listing import generate_listing
from worker.activities.submit_draft import submit_draft


def test_required_taxonomy_maps_case_insensitively():
    assert map_category("Coats") == "1005"
    assert map_size("xxl") == "XXL" and map_size("47") == "47"
    assert map_color("black") == "BLACK"
    assert map_condition("very good") == "VERY_GOOD"


def test_unknown_taxonomy_is_rejected():
    with pytest.raises(ValueError): map_category("unknown")
    with pytest.raises(ValueError): map_size("48")


def test_listing_contract_enforces_tradera_semantics():
    with pytest.raises(ValueError):
        ListingPayload(title="x", description="d", category_id="1", condition="USED_GOOD", size="M", price=1, currency="EUR", photo_refs=["x"])
    with pytest.raises(ValueError):
        ListingPayload(title="x", description="d", category_id="1", condition="USED_GOOD", size="M", price=1, inactive=False, photo_refs=["x"])


@pytest.mark.asyncio
async def test_price_research_prefers_sold_and_uses_sek_rounding():
    class Client:
        async def search_listings(self, query):
            return [{"price": 101, "title": "sold"}, {"price": 199, "title": "sold"}] if query["status"] == "sold" else [{"price": 1}]
    product = Product(model="Coat", category="coats", category_id="1005", condition="USED_GOOD", size="M", photo_blobs=["x"])
    result = await research_price(product, Client())
    assert result.source == "sold" and result.recommended_price_sek == Decimal("150")


def test_generator_limits_title_and_sets_sek():
    product = Product(brand="A", model="B" * 100, category="tops", category_id="1001", condition="USED_GOOD", size="M", photo_blobs=["x"])
    price = PriceResearch(recommended_price_sek=100, sample_count=1, source="fallback", confidence="low")
    result = generate_listing(product, price)
    assert len(result.title) <= 80 and result.currency == "SEK" and result.inactive and result.fixed_price


@pytest.mark.asyncio
async def test_submit_draft_creates_then_uploads_all_photos(tmp_path):
    first, second = tmp_path / "a.jpg", tmp_path / "b.jpg"
    first.write_bytes(b"a"); second.write_bytes(b"b")
    class Client:
        def __init__(self): self.uploads = []
        async def create_listing(self, listing):
            from worker.shared.models import DraftResult
            return DraftResult(draft_id="d", tradera_draft_url="https://tradera.test/d")
        async def upload_photo(self, listing_id, image): self.uploads.append((listing_id, image))
    listing = ListingPayload(title="x", description="d", category_id="1", condition="USED_GOOD", size="M", price=1, photo_refs=[str(first), str(second)])
    client = Client(); result = await submit_draft(listing, client)
    assert result.photo_count == 2 and len(client.uploads) == 2
