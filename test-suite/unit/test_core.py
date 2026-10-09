import io
from decimal import Decimal
from PIL import Image
import pytest
from worker.shared.models import ListingPayload, Product, Condition
from worker.activities.analyse_product import parse_folder_name
from worker.activities.generate_listing import generate_listing
from worker.shared.exif_stripper import strip_exif

@pytest.mark.parametrize("value,expected", [
    ("Vinted/Dressmann Jean Shirt Size M", ("Dressmann Jean", "Shirt", "M")),
    ("Nike Air Max 90 Size 42", ("Nike Air Max", "90", "42")),
    ("Zara Dress XXL", ("Zara", "Dress", "XXL")),
])
def test_parse_folder_formats(value, expected):
    assert parse_folder_name(value) == expected

def test_parse_folder_rejects_missing_size():
    with pytest.raises(ValueError, match="unambiguous"):
        parse_folder_name("Nike Air Max")

def test_models_validate_positive_price_and_title():
    with pytest.raises(ValueError):
        ListingPayload(title="x", description="d", price=0, category_id="1001", condition="GOOD", size="M", photo_refs=["a"])
    with pytest.raises(ValueError):
        ListingPayload(title="x" * 81, description="d", price=1, category_id="1001", condition="GOOD", size="M", photo_refs=["a"])

def test_listing_generation_uses_price_and_limits_title():
    product = Product(brand="A", model="Very long model name " * 5, size="M", category="tops", category_id="1001", condition=Condition.GOOD, photo_blobs=["photo.jpg"])
    pricing = type("Price", (), {"recommended_price_sek": Decimal("18")})()
    listing = generate_listing(product, pricing)
    assert len(listing.title) <= 80 and listing.currency == "SEK"
    assert listing.price == Decimal("18")

def test_exif_stripper_keeps_pixels_and_removes_metadata():
    image = Image.new("RGB", (2, 2), "red")
    buf = io.BytesIO(); image.save(buf, format="JPEG", exif=Image.Exif())
    result = strip_exif(buf.getvalue())
    with Image.open(io.BytesIO(result)) as cleaned:
        assert cleaned.size == (2, 2)
        assert len(cleaned.getexif()) == 0
