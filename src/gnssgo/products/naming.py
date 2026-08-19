from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date, datetime

from gnssgo.models import (
    BiasProductKind,
    ProductDescriptor,
    ProductRequest,
    ProductSystem,
    ProductTier,
    ProductType,
)
from gnssgo.utils.dates import datetime_to_doy
from gnssgo.utils.gps_time import datetime_to_gpsweek, gpsweek_to_datetime

IGS_LONG_FILENAME_TRANSITION = date(2022, 11, 27)
IGS_LONG_FILENAME_TRANSITION_GPS_WEEK = 2238

SOLUTION_TO_TIER = {
    "FIN": ProductTier.FINAL,
    "RAP": ProductTier.RAPID,
    "ULT": ProductTier.ULTRA,
    "NRT": ProductTier.RAPID,
    "PRD": ProductTier.PREDICTED,
    "RTS": ProductTier.REALTIME,
    "SNX": ProductTier.FINAL,
}

TIER_TO_SOLUTION = {
    ProductTier.FINAL: "FIN",
    ProductTier.RAPID: "RAP",
    ProductTier.ULTRA: "ULT",
}

CONTENT_TO_PRODUCT = {
    "ORB": ProductType.ORBIT,
    "CLK": ProductType.CLOCK,
    "ERP": ProductType.ERP,
    "BIA": ProductType.BIAS,
    "OSB": ProductType.BIAS,
    "DSB": ProductType.BIAS,
    "DCB": ProductType.BIAS,
    "ION": ProductType.IONEX,
    "GIM": ProductType.IONEX,
    "SOL": ProductType.SINEX,
    "SNX": ProductType.SINEX,
    "ATX": ProductType.ANTEX,
}

FORMAT_TO_PRODUCT = {
    "SP3": ProductType.ORBIT,
    "CLK": ProductType.CLOCK,
    "ERP": ProductType.ERP,
    "BIA": ProductType.BIAS,
    "BSX": ProductType.BIAS,
    "IONEX": ProductType.IONEX,
    "INX": ProductType.IONEX,
    "ION": ProductType.IONEX,
    "I": ProductType.IONEX,
    "SNX": ProductType.SINEX,
    "ATX": ProductType.ANTEX,
}

LEGACY_PREFIX_TO_CENTER = {
    "igs": "IGS",
    "igr": "IGS",
    "igu": "IGS",
    "cod": "COD",
    "cof": "COD",
    "cor": "COD",
    "gfz": "GFZ",
    "grg": "GRG",
    "esa": "ESA",
    "wum": "WUM",
    "wuh": "WUM",
    "jpl": "JPL",
    "emr": "EMR",
}

LEGACY_PREFIX_TO_TIER = {
    "igs": ProductTier.FINAL,
    "cod": ProductTier.FINAL,
    "gfz": ProductTier.FINAL,
    "grg": ProductTier.FINAL,
    "esa": ProductTier.FINAL,
    "wum": ProductTier.FINAL,
    "wuh": ProductTier.FINAL,
    "jpl": ProductTier.FINAL,
    "emr": ProductTier.FINAL,
    "igr": ProductTier.RAPID,
    "cof": ProductTier.RAPID,
    "cor": ProductTier.RAPID,
    "igu": ProductTier.ULTRA,
}

LEGACY_PRODUCT_RE = re.compile(
    r"^(?P<prefix>[a-z0-9]{3})(?P<week>\d{4})(?P<dow>[0-7])"
    r"(?:_(?P<hour>\d{2}))?\.(?P<fmt>sp3|clk|erp|snx|bia|bsx)"
    r"(?P<compression>\.Z|\.z|\.gz)?$",
    re.IGNORECASE,
)

LEGACY_IONEX_RE = re.compile(
    r"^(?P<prefix>[a-z]{4})(?P<doy>\d{3})0\.(?P<year2>\d{2})i"
    r"(?P<compression>\.Z|\.z|\.gz)?$",
    re.IGNORECASE,
)


def use_long_filename_for_igs_operational(day: date) -> bool:
    return day >= IGS_LONG_FILENAME_TRANSITION


def parse_product_filename(filename: str) -> ProductDescriptor | None:
    return parse_long_product_filename(filename) or parse_legacy_product_filename(filename)


def parse_long_product_filename(filename: str) -> ProductDescriptor | None:
    base, compression = _strip_compression(filename)
    if "_" not in base or "." not in base:
        return None
    stem, fmt = base.rsplit(".", 1)
    parts = stem.split("_")
    if len(parts) < 5:
        return None
    head, epoch_raw, duration, sampling = parts[:4]
    content = parts[-1]
    head_match = re.match(
        r"^(?P<center>[A-Z0-9]{3})(?P<version>[A-Z0-9])"
        r"(?P<campaign>[A-Z0-9]{3})(?P<solution>[A-Z0-9]{3})$",
        head,
        flags=re.IGNORECASE,
    )
    if not head_match or not re.match(r"^\d{11,12}$", epoch_raw):
        return None
    fmt = fmt.upper()
    content = content.upper()
    product_type = CONTENT_TO_PRODUCT.get(content) or FORMAT_TO_PRODUCT.get(fmt)
    if not product_type:
        return None
    start_epoch = _parse_long_epoch(epoch_raw)
    solution = head_match.group("solution").upper()
    center = _normalize_center(head_match.group("center").upper())
    bias_kind = _bias_kind(content)
    return ProductDescriptor(
        product_type=product_type,
        center=center,
        tier=SOLUTION_TO_TIER.get(solution, ProductTier.AUTO),
        system=ProductSystem.MULTI,
        start_epoch=start_epoch,
        date=start_epoch.date(),
        duration=duration.upper(),
        sampling=sampling.upper(),
        format=fmt,
        campaign=head_match.group("campaign").upper(),
        content=content,
        bias_kind=bias_kind,
        filename=filename,
        compression=compression,
    )


def parse_legacy_product_filename(filename: str) -> ProductDescriptor | None:
    base, compression = _strip_compression(filename)
    match = LEGACY_PRODUCT_RE.match(filename) or LEGACY_PRODUCT_RE.match(base)
    if match:
        prefix = match.group("prefix").lower()
        fmt = match.group("fmt").upper()
        product_type = FORMAT_TO_PRODUCT.get(fmt)
        if not product_type:
            return None
        week = int(match.group("week"))
        dow = int(match.group("dow"))
        hour = int(match.group("hour") or 0)
        start_epoch = gpsweek_to_datetime(week, min(dow, 6)).replace(hour=hour)
        center = LEGACY_PREFIX_TO_CENTER.get(prefix, prefix.upper())
        return ProductDescriptor(
            product_type=product_type,
            center=center,
            tier=LEGACY_PREFIX_TO_TIER.get(prefix, ProductTier.FINAL),
            system=ProductSystem.GPS,
            start_epoch=start_epoch,
            date=start_epoch.date(),
            duration="01D" if product_type != ProductType.ERP else "07D",
            sampling=None,
            format=fmt,
            campaign="LEG",
            content=fmt,
            filename=filename,
            compression=compression or match.group("compression"),
        )

    ionex = LEGACY_IONEX_RE.match(filename) or LEGACY_IONEX_RE.match(base)
    if ionex:
        prefix = ionex.group("prefix").lower()
        year2 = int(ionex.group("year2"))
        year = 2000 + year2 if year2 < 80 else 1900 + year2
        day = datetime.strptime(f"{year}{int(ionex.group('doy')):03d}", "%Y%j").date()
        center = "IGS" if prefix.startswith("igs") else prefix[:3].upper()
        return ProductDescriptor(
            product_type=ProductType.IONEX,
            center=center,
            tier=ProductTier.FINAL,
            system=ProductSystem.MULTI,
            start_epoch=datetime.combine(day, datetime.min.time()),
            date=day,
            duration="01D",
            sampling=None,
            format="IONEX",
            campaign="LEG",
            content="ION",
            filename=filename,
            compression=compression or ionex.group("compression"),
        )
    return None


def product_matches_request(
    descriptor: ProductDescriptor,
    request: ProductRequest,
    day: date,
) -> bool:
    if descriptor.product_type not in request.product_types:
        return False
    if descriptor.date and descriptor.date != day:
        if descriptor.product_type == ProductType.ERP and descriptor.date <= day:
            pass
        else:
            return False
    if request.tier != ProductTier.AUTO and descriptor.tier != request.tier:
        return False
    if request.center.lower() != "auto" and descriptor.center != request.center.upper():
        return False
    if request.system == ProductSystem.MULTI and descriptor.system == ProductSystem.GPS:
        return False
    if request.system == ProductSystem.GPS and descriptor.system == ProductSystem.MULTI:
        return False
    return not (
        request.sampling
        and descriptor.sampling
        and descriptor.sampling != request.sampling.upper()
    )


class ProductNamingRule(ABC):
    @abstractmethod
    def candidates(
        self,
        day: date,
        product_type: ProductType,
        request: ProductRequest,
    ) -> list[str]:
        raise NotImplementedError


class IGSLongProductRule(ProductNamingRule):
    def candidates(
        self,
        day: date,
        product_type: ProductType,
        request: ProductRequest,
    ) -> list[str]:
        if not use_long_filename_for_igs_operational(day):
            return []
        tier = request.tier if request.tier != ProductTier.AUTO else ProductTier.FINAL
        centers = [request.center.upper()] if request.center.lower() != "auto" else ["IGS"]
        # Ionosphere products are special: individual IAAC products can have
        # different temporal resolutions.  For automatic selection, include
        # the currently modeled IGS-combined and CODE product families so a
        # requested temporal interval can resolve to the appropriate center.
        if product_type == ProductType.IONEX and request.center.lower() == "auto":
            centers = ["IGS", "COD"]
        names: list[str] = []
        doy = datetime_to_doy(day)

        # IGS combined terrestrial-frame SINEX uses the SNX solution code,
        # rather than FIN/RAP/ULT. Other AC SINEX products are discovered from
        # provider directory listings instead of being guessed here.
        if product_type == ProductType.SINEX:
            if tier != ProductTier.FINAL or centers != ["IGS"]:
                return []
            epoch = f"{day.year}{doy:03d}0000"
            return [f"IGS0OPSSNX_{epoch}_01D_01D_SOL.SNX.gz"]

        solution = TIER_TO_SOLUTION.get(tier)
        if not solution:
            return []
        for center in centers:
            center = "WUM" if center == "WUH" else center
            head = f"{center}0OPS{solution}"
            for hour in _hours_for_tier(tier):
                epoch = f"{day.year}{doy:03d}{hour:02d}00"
                for duration, sampling, content, fmt in _long_specs(
                    product_type, tier, center=center
                ):
                    if request.sampling and sampling != request.sampling.upper():
                        continue
                    names.append(f"{head}_{epoch}_{duration}_{sampling}_{content}.{fmt}.gz")
        return names


class IGSLegacyProductRule(ProductNamingRule):
    def candidates(
        self,
        day: date,
        product_type: ProductType,
        request: ProductRequest,
    ) -> list[str]:
        if use_long_filename_for_igs_operational(day):
            return []
        tier = request.tier if request.tier != ProductTier.AUTO else ProductTier.FINAL
        gps_week, dow = datetime_to_gpsweek(day)
        prefix = {
            ProductTier.FINAL: "igs",
            ProductTier.RAPID: "igr",
            ProductTier.ULTRA: "igu",
        }.get(tier)
        if not prefix:
            return []
        if product_type == ProductType.ORBIT:
            if tier == ProductTier.ULTRA:
                return [f"{prefix}{gps_week}{dow}_{hour:02d}.sp3.Z" for hour in (0, 6, 12, 18)]
            return [f"{prefix}{gps_week}{dow}.sp3.Z"]
        if product_type == ProductType.CLOCK and tier != ProductTier.ULTRA:
            return [f"{prefix}{gps_week}{dow}.clk.Z"]
        if product_type == ProductType.ERP and tier != ProductTier.ULTRA:
            return [f"{prefix}{gps_week}7.erp.Z"]
        if product_type == ProductType.IONEX:
            doy = datetime_to_doy(day)
            return [f"igsg{doy:03d}0.{day.year % 100:02d}i.Z"]
        return []


class ProductNamingRegistry:
    def __init__(self) -> None:
        self._rules: list[ProductNamingRule] = []
        self.register(IGSLongProductRule())
        self.register(IGSLegacyProductRule())

    def register(self, rule: ProductNamingRule) -> None:
        self._rules.append(rule)

    def candidates(
        self,
        day: date,
        product_type: ProductType,
        request: ProductRequest,
    ) -> list[str]:
        names: list[str] = []
        for rule in self._rules:
            names.extend(rule.candidates(day, product_type, request))
        return list(dict.fromkeys(names))


def _strip_compression(filename: str) -> tuple[str, str | None]:
    for suffix in (".gz", ".Z", ".z"):
        if filename.endswith(suffix):
            return filename[: -len(suffix)], suffix
    return filename, None


def _parse_long_epoch(value: str) -> datetime:
    if len(value) > 11:
        value = value[:11]
    return datetime.strptime(value, "%Y%j%H%M")


def _normalize_center(center: str) -> str:
    return "WUM" if center == "WUH" else center


def _bias_kind(content: str) -> BiasProductKind | None:
    return {
        "BIA": BiasProductKind.SINEX_BIAS,
        "OSB": BiasProductKind.OSB,
        "DSB": BiasProductKind.DSB,
        "DCB": BiasProductKind.DCB,
    }.get(content)


def _hours_for_tier(tier: ProductTier) -> tuple[int, ...]:
    if tier == ProductTier.ULTRA:
        return (0, 6, 12, 18)
    return (0,)


def _long_specs(
    product_type: ProductType, tier: ProductTier, *, center: str | None = None
) -> list[tuple[str, str, str, str]]:
    duration = "02D" if tier == ProductTier.ULTRA else "01D"
    if product_type == ProductType.ORBIT:
        return [(duration, "15M", "ORB", "SP3")]
    if product_type == ProductType.CLOCK:
        if tier == ProductTier.FINAL:
            return [
                ("01D", "05M", "CLK", "CLK"),
                ("01D", "30S", "CLK", "CLK"),
            ]
        if tier == ProductTier.RAPID:
            return [("01D", "05M", "CLK", "CLK")]
        return []
    if product_type == ProductType.ERP:
        if tier == ProductTier.FINAL:
            return [("07D", "01D", "ERP", "ERP")]
        if tier == ProductTier.RAPID:
            return [("01D", "01D", "ERP", "ERP")]
        if tier == ProductTier.ULTRA:
            return [("02D", "01D", "ERP", "ERP")]
        return []
    return {
        ProductType.BIAS: [
            ("01D", "01D", "OSB", "BIA"),
            ("01D", "01D", "DCB", "BSX"),
        ],
        ProductType.IONEX: [
            ("01D", "01H", "GIM", "INX")
            if (center or "").upper() == "COD"
            else ("01D", "02H", "GIM", "INX")
        ],
        ProductType.SINEX: [],
        ProductType.ANTEX: [],
    }.get(product_type, [])

