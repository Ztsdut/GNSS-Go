from datetime import date

from gnssgo.models import DateRange, ProductRequest, ProductTier, ProductType
from gnssgo.products.naming import (
    IGS_LONG_FILENAME_TRANSITION,
    IGS_LONG_FILENAME_TRANSITION_GPS_WEEK,
    ProductNamingRegistry,
    parse_product_filename,
    use_long_filename_for_igs_operational,
)
from gnssgo.utils.gps_time import datetime_to_gpsweek, gpsweek_to_datetime


def test_parse_modern_long_orbit_clock_erp_bias_ionex() -> None:
    fixtures = {
        "IGS0OPSFIN_20262130000_01D_15M_ORB.SP3.gz": ProductType.ORBIT,
        "COD0OPSRAP_20262130000_01D_30S_CLK.CLK.gz": ProductType.CLOCK,
        "GFZ0OPSFIN_20262130000_01D_01D_ERP.ERP.gz": ProductType.ERP,
        "WUM0MGXRAP_20262130000_01D_01D_OSB.BIA.gz": ProductType.BIAS,
        "CAS0MGXRAP_20262130000_01D_02H_GIM.INX.gz": ProductType.IONEX,
    }
    for filename, expected in fixtures.items():
        parsed = parse_product_filename(filename)
        assert parsed is not None
        assert parsed.product_type == expected
        assert parsed.date == date(2026, 8, 1)


def test_parse_legacy_product_names() -> None:
    for filename, product_type, tier in [
        ("igs22376.sp3.Z", ProductType.ORBIT, ProductTier.FINAL),
        ("igr22376.clk.Z", ProductType.CLOCK, ProductTier.RAPID),
        ("igu22376_12.sp3.Z", ProductType.ORBIT, ProductTier.ULTRA),
        ("igs22377.erp.Z", ProductType.ERP, ProductTier.FINAL),
        ("igsg3310.22i.Z", ProductType.IONEX, ProductTier.FINAL),
    ]:
        parsed = parse_product_filename(filename)
        assert parsed is not None
        assert parsed.product_type == product_type
        assert parsed.tier == tier


def test_gps_week_2238_transition_boundary() -> None:
    assert datetime_to_gpsweek(IGS_LONG_FILENAME_TRANSITION)[0] == (
        IGS_LONG_FILENAME_TRANSITION_GPS_WEEK
    )
    assert not use_long_filename_for_igs_operational(date(2022, 11, 26))
    assert use_long_filename_for_igs_operational(date(2022, 11, 27))

    request = ProductRequest(
        date_range=DateRange(start="2022-11-27", end="2022-11-27"),
        product_types=[ProductType.ORBIT],
        tier="final",
    )
    names = ProductNamingRegistry().candidates(date(2022, 11, 27), ProductType.ORBIT, request)
    assert any(name.startswith("IGS0OPSFIN_2022331") for name in names)


def test_gps_week_round_trip_week_boundary() -> None:
    week, dow = datetime_to_gpsweek(date(2026, 8, 1))
    assert gpsweek_to_datetime(week, dow).date() == date(2026, 8, 1)
    assert datetime_to_gpsweek(date(2026, 8, 2))[0] == week + 1


def test_igs_final_clock_offers_both_official_intervals() -> None:
    request = ProductRequest(
        date_range=DateRange(start="2026-08-14", end="2026-08-14"),
        product_types=[ProductType.CLOCK],
        tier="final",
    )
    names = ProductNamingRegistry().candidates(
        date(2026, 8, 14), ProductType.CLOCK, request
    )
    assert any("_05M_CLK.CLK.gz" in name for name in names)
    assert any("_30S_CLK.CLK.gz" in name for name in names)


def test_igs_combined_sinex_uses_snx_solution_code() -> None:
    request = ProductRequest(
        date_range=DateRange(start="2026-08-14", end="2026-08-14"),
        product_types=[ProductType.SINEX],
        tier="final",
    )
    names = ProductNamingRegistry().candidates(
        date(2026, 8, 14), ProductType.SINEX, request
    )
    assert names == ["IGS0OPSSNX_20262260000_01D_01D_SOL.SNX.gz"]


def test_ionex_long_temporal_resolution_by_center():
    from datetime import date

    from gnssgo.models import DateRange, ProductRequest, ProductType
    from gnssgo.products.naming import ProductNamingRegistry

    day = date(2026, 8, 14)
    registry = ProductNamingRegistry()

    igs = ProductRequest(
        date_range=DateRange(start=day, end=day),
        product_types=[ProductType.IONEX],
        center="IGS",
        tier="final",
    )
    cod = ProductRequest(
        date_range=DateRange(start=day, end=day),
        product_types=[ProductType.IONEX],
        center="COD",
        tier="final",
    )
    auto_1h = ProductRequest(
        date_range=DateRange(start=day, end=day),
        product_types=[ProductType.IONEX],
        center="auto",
        tier="final",
        sampling="01H",
    )

    igs_names = registry.candidates(day, ProductType.IONEX, igs)
    cod_names = registry.candidates(day, ProductType.IONEX, cod)
    auto_names = registry.candidates(day, ProductType.IONEX, auto_1h)

    assert any("_02H_GIM.INX.gz" in name for name in igs_names)
    assert any(name.startswith("COD0OPSFIN_") and "_01H_GIM.INX.gz" in name for name in cod_names)
    assert auto_names
    assert all("_01H_GIM.INX.gz" in name for name in auto_names)
    assert all(name.startswith("COD0OPSFIN_") for name in auto_names)
