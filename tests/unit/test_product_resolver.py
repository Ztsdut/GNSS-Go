from gnssgo.models import DateRange, ProductRequest, ProductType
from gnssgo.products import ProductResolver


def test_product_resolver_keeps_available_rules() -> None:
    request = ProductRequest(
        date_range=DateRange(start="2026-08-01", end="2026-08-01"),
        product_types=[ProductType.ORBIT, ProductType.CLOCK],
    )
    rules = ProductResolver().resolve(request)
    assert [rule.product_type for rule in rules] == [ProductType.ORBIT, ProductType.CLOCK]
