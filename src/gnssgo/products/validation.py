from __future__ import annotations

from pathlib import Path

from gnssgo.models import ProductType, ValidationResult
from gnssgo.products.naming import FORMAT_TO_PRODUCT, parse_product_filename


def validate_product_file(
    path: Path,
    product_type: ProductType | str | None = None,
) -> ValidationResult:
    expected = ProductType(product_type) if product_type else _infer_type(path)
    if path.stat().st_size == 0:
        return ValidationResult(valid=False, product_type=expected, error="Product file is empty.")
    head = _read_head(path)
    if "<html" in head.lower() or "<!doctype html" in head.lower():
        return ValidationResult(
            valid=False,
            product_type=expected,
            error="Remote returned HTML instead of product data.",
        )
    validators = {
        ProductType.ORBIT: _validate_sp3,
        ProductType.CLOCK: _validate_clock,
        ProductType.ERP: _validate_erp,
        ProductType.BIAS: _validate_bias,
        ProductType.IONEX: _validate_ionex,
        ProductType.SINEX: _validate_sinex,
        ProductType.ANTEX: _validate_antex,
    }
    return validators[expected](path, head)


def _infer_type(path: Path) -> ProductType:
    parsed = parse_product_filename(path.name)
    if parsed:
        return parsed.product_type
    suffix = path.suffix.upper().lstrip(".")
    return FORMAT_TO_PRODUCT.get(suffix, ProductType.ORBIT)


def _read_head(path: Path, size: int = 262144) -> str:
    return path.read_bytes()[:size].decode("ascii", errors="ignore")


def _validate_sp3(path: Path, head: str) -> ValidationResult:
    valid = head.startswith("#") and ("+   " in head or "\n*" in head)
    return ValidationResult(
        valid=valid,
        product_type=ProductType.ORBIT,
        metadata={"format": "SP3", "header": head.splitlines()[0][:80] if head else ""},
        error=None if valid else "SP3 header or epoch records were not detected.",
    )


def _validate_clock(path: Path, head: str) -> ValidationResult:
    valid = "RINEX VERSION / TYPE" in head and "CLOCK" in head.upper()
    return ValidationResult(
        valid=valid,
        product_type=ProductType.CLOCK,
        metadata={"format": "CLK"},
        error=None if valid else "RINEX CLOCK header was not detected.",
    )


def _validate_erp(path: Path, head: str) -> ValidationResult:
    lines = [line for line in head.splitlines() if line.strip()]
    has_numeric = any(any(char.isdigit() for char in line) for line in lines[:20])
    valid = bool(lines) and has_numeric
    return ValidationResult(
        valid=valid,
        product_type=ProductType.ERP,
        metadata={"format": "ERP"},
        error=None if valid else "ERP numeric content was not detected.",
    )


def _validate_bias(path: Path, head: str) -> ValidationResult:
    upper = head.upper()
    valid = "BIAS" in upper or "%=BIA" in upper or "SINEX" in upper
    return ValidationResult(
        valid=valid,
        product_type=ProductType.BIAS,
        metadata={"format": "BIA"},
        error=None if valid else "SINEX BIAS/BIA content was not detected.",
    )


def _validate_ionex(path: Path, head: str) -> ValidationResult:
    valid = "IONEX VERSION / TYPE" in head and "END OF HEADER" in head
    return ValidationResult(
        valid=valid,
        product_type=ProductType.IONEX,
        metadata={"format": "IONEX"},
        error=None if valid else "IONEX header was not detected.",
    )


def _validate_sinex(path: Path, head: str) -> ValidationResult:
    valid = head.startswith("%=SNX") or "SOLUTION/" in head.upper()
    return ValidationResult(
        valid=valid,
        product_type=ProductType.SINEX,
        metadata={"format": "SNX"},
        error=None if valid else "SINEX header was not detected.",
    )


def _validate_antex(path: Path, head: str) -> ValidationResult:
    valid = "ANTEX VERSION / SYST" in head or "START OF ANTENNA" in head
    return ValidationResult(
        valid=valid,
        product_type=ProductType.ANTEX,
        metadata={"format": "ATX"},
        error=None if valid else "ANTEX header was not detected.",
    )
