from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gnssgo.exceptions import ValidationError


@dataclass(frozen=True)
class RinexValidationResult:
    path: Path
    version: str
    rinex_type: str


def looks_like_rinex(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as handle:
        head = handle.read(4096)
    return b"RINEX VERSION / TYPE" in head or path.suffix.lower() in {".rnx", ".crx"}


def validate_rinex_file(path: Path, expected_type: str | None = None) -> RinexValidationResult:
    if not path.exists() or path.stat().st_size == 0:
        raise ValidationError(f"RINEX file is missing or empty: {path}")

    with path.open("rb") as handle:
        for raw_line in handle:
            line = raw_line.decode("ascii", errors="ignore").rstrip("\r\n")
            if "RINEX VERSION / TYPE" not in line:
                continue
            version = line[:20].strip()
            type_field = line[20:40].strip().upper()
            rinex_type = _normalize_type(type_field)
            if expected_type and rinex_type != expected_type:
                raise ValidationError(
                    f"Expected RINEX {expected_type}, got {rinex_type} in {path.name}."
                )
            return RinexValidationResult(path=path, version=version, rinex_type=rinex_type)

    raise ValidationError(f"Missing RINEX VERSION / TYPE header in {path.name}.")


def _normalize_type(value: str) -> str:
    if "OBSERVATION" in value or value.startswith("O"):
        return "observation"
    if "NAVIGATION" in value or value.startswith("N"):
        return "navigation"
    return value.lower() or "unknown"
