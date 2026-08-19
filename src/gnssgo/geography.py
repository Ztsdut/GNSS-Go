from __future__ import annotations

# Geographic grouping used only by the GUI/network filter.  It intentionally
# follows GNSS Go's product taxonomy (Mexico/Central America/Caribbean are in
# Latin America) rather than a strict physical-continent convention.
_CONTINENT_COUNTRIES: dict[str, set[str]] = {
    "Africa": {
        "DZA","AGO","BEN","BWA","BFA","BDI","CPV","CMR","CAF","TCD","COM","COG","COD","CIV","DJI","EGY","GNQ","ERI","SWZ","ETH","GAB","GMB","GHA","GIN","GNB","KEN","LSO","LBR","LBY","MDG","MWI","MLI","MRT","MUS","MAR","MOZ","NAM","NER","NGA","RWA","STP","SEN","SYC","SLE","SOM","ZAF","SSD","SDN","TZA","TGO","TUN","UGA","ZMB","ZWE","ESH","REU","MYT",
    },
    "Antarctica": {"ATA"},
    "Asia": {
        "AFG","ARM","AZE","BHR","BGD","BTN","BRN","KHM","CHN","CYP","GEO","HKG","IND","IDN","IRN","IRQ","ISR","JPN","JOR","KAZ","KWT","KGZ","LAO","LBN","MAC","MYS","MDV","MNG","MMR","NPL","PRK","OMN","PAK","PSE","PHL","QAT","SAU","SGP","KOR","LKA","SYR","TWN","TJK","THA","TLS","TUR","TKM","ARE","UZB","VNM","YEM",
    },
    "Europe": {
        "ALB","AND","AUT","BLR","BEL","BIH","BGR","HRV","CZE","DNK","EST","FIN","FRA","DEU","GRC","HUN","ISL","IRL","ITA","XKX","LVA","LIE","LTU","LUX","MLT","MDA","MCO","MNE","NLD","MKD","NOR","POL","PRT","ROU","RUS","SMR","SRB","SVK","SVN","ESP","SWE","CHE","UKR","GBR","VAT","FRO","GGY","IMN","JEY",
    },
    "Latin America": {
        "ARG","BOL","BRA","CHL","COL","ECU","GUY","PRY","PER","SUR","URY","VEN",
        "MEX","BLZ","CRI","SLV","GTM","HND","NIC","PAN",
        "ATG","BHS","BRB","CUB","DMA","DOM","GRD","HTI","JAM","KNA","LCA","VCT","TTO",
        "ABW","BES","CUW","GLP","MTQ","PRI","SXM","VGB","VIR","AIA","MSR","TCA","CYM",
    },
    "North America": {"USA","CAN","GRL","BMU","SPM"},
    "Oceania": {
        "AUS","NZL","FJI","PNG","SLB","VUT","WSM","TON","KIR","TUV","NRU","PLW","MHL","FSM",
        "GUM","ASM","MNP","NCL","PYF","COK","NIU","TKL","WLF","NFK","CCK","CXR","PCN",
    },
}


def continent_for_country(country: str | None) -> str | None:
    code = str(country or "").upper().strip()
    if not code:
        return None
    for continent, countries in _CONTINENT_COUNTRIES.items():
        if code in countries:
            return continent
    return None


def countries_for_continents(continents: list[str] | set[str] | tuple[str, ...]) -> set[str]:
    result: set[str] = set()
    for continent in continents:
        result.update(_CONTINENT_COUNTRIES.get(str(continent), set()))
    return result
