from __future__ import annotations

from gnssgo.models import AnalysisCenter


class AnalysisCenterRegistry:
    def __init__(self) -> None:
        self._centers: dict[str, AnalysisCenter] = {}
        for center in _DEFAULT_CENTERS:
            self.register(center)

    def register(self, center: AnalysisCenter) -> None:
        self._centers[center.code.upper()] = center
        for alias in center.aliases:
            self._centers[alias.upper()] = center

    def get(self, code: str) -> AnalysisCenter | None:
        if code.lower() == "auto":
            return None
        return self._centers.get(code.upper())

    def normalize(self, code: str) -> str:
        center = self.get(code)
        return center.code if center else code.upper()

    def centers(self) -> list[AnalysisCenter]:
        unique: dict[str, AnalysisCenter] = {}
        for center in self._centers.values():
            unique[center.code] = center
        return list(unique.values())


_DEFAULT_CENTERS = [
    AnalysisCenter(code="IGS", aliases=["IGS0"], supports_multi_gnss=True),
    AnalysisCenter(code="COD", aliases=["CODE"], supports_multi_gnss=True),
    AnalysisCenter(code="GFZ", supports_multi_gnss=True),
    AnalysisCenter(code="ESA", aliases=["ESOC"], supports_multi_gnss=True),
    AnalysisCenter(code="GRG", aliases=["CNES"], supports_multi_gnss=True),
    AnalysisCenter(code="JPL", supports_multi_gnss=False),
    AnalysisCenter(code="WUM", aliases=["WUH"], supports_multi_gnss=True),
    AnalysisCenter(code="EMR", supports_multi_gnss=False),
    AnalysisCenter(code="NGS", supports_multi_gnss=False),
    AnalysisCenter(code="SIO", supports_multi_gnss=False),
    AnalysisCenter(code="MIT", supports_multi_gnss=False),
    AnalysisCenter(code="JAX", supports_multi_gnss=True),
    AnalysisCenter(code="SHA", supports_multi_gnss=True),
    AnalysisCenter(code="CAS", supports_multi_gnss=True),
]
