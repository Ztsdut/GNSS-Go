from pathlib import Path

from gnssgo.models import ProductType
from gnssgo.products import ProductPresetRegistry, validate_product_file


def test_product_presets() -> None:
    registry = ProductPresetRegistry()
    assert [item.value for item in registry.get("ppp").product_types] == [
        "orbit",
        "clock",
        "erp",
        "bias",
    ]
    assert [item.value for item in registry.get("ionosphere").product_types] == [
        "ionex",
        "bias",
    ]


def test_product_validators(tmp_path: Path) -> None:
    files = {
        ProductType.ORBIT: (
            "#cP2026 08 01 00 00 00.00000000\n"
            "+    1 G01\n"
            "*  2026  8  1  0  0  0.00000000\n"
        ),
        ProductType.CLOCK: (
            "     3.04           C                   RINEX VERSION / TYPE\n"
            "CLOCK DATA\n"
        ),
        ProductType.ERP: "version 2\n  60000  1.0  2.0  3.0\n",
        ProductType.BIAS: "%=BIA 1.00 GNSSGO\n+BIAS/SOLUTION\n",
        ProductType.IONEX: "     1.0            IONEX VERSION / TYPE\nEND OF HEADER\n",
    }
    for product_type, content in files.items():
        path = tmp_path / f"sample.{product_type.value}"
        path.write_text(content, encoding="ascii")
        result = validate_product_file(path, product_type)
        assert result.valid, result.error
