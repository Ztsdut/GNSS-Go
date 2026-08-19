from __future__ import annotations

from pydantic import BaseModel, Field

from gnssgo.models import ProductType


class ProductPreset(BaseModel):
    name: str
    product_types: list[ProductType] = Field(default_factory=list)
    description: str = ""


class ProductPresetRegistry:
    def __init__(self) -> None:
        self._presets: dict[str, ProductPreset] = {}
        self.register(
            ProductPreset(
                name="ppp",
                product_types=[
                    ProductType.ORBIT,
                    ProductType.CLOCK,
                    ProductType.ERP,
                    ProductType.BIAS,
                ],
                description="Precise orbit, clock, ERP, and bias products for PPP preparation.",
            )
        )
        self.register(
            ProductPreset(
                name="ionosphere",
                product_types=[ProductType.IONEX, ProductType.BIAS],
                description="IONEX grid and bias products for ionosphere workflows.",
            )
        )

    def register(self, preset: ProductPreset) -> None:
        self._presets[preset.name.lower()] = preset

    def get(self, name: str) -> ProductPreset:
        key = name.lower()
        if key not in self._presets:
            valid = ", ".join(sorted(self._presets))
            raise ValueError(f"Unknown product preset {name!r}. Available presets: {valid}.")
        return self._presets[key]


PPP_PRODUCTS = ProductPresetRegistry().get("ppp").product_types
IONOSPHERE_PRODUCTS = ProductPresetRegistry().get("ionosphere").product_types
