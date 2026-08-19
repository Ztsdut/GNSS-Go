from __future__ import annotations

import re
from dataclasses import dataclass

from gnssgo.exceptions import ConfigurationError


@dataclass(frozen=True)
class RegionalSource:
    id: str
    name: str
    data_network: str
    provider: str


class RegionalSourceRegistry:
    def __init__(self, sources: list[RegionalSource] | None = None) -> None:
        self._sources = {source.id: source for source in sources or default_sources()}
        self._aliases: dict[str, str] = {}
        for source in self._sources.values():
            for value in {source.id, source.name}:
                self._aliases[_source_key(value)] = source.id

        # Compatibility with the earlier Europe layout.  ROB/BEV/BKG/IGN are
        # physical EPN data centres, not station networks.  They remain internal
        # fallback servers of EPNProvider; old saved GUI filters are folded into
        # the logical EPN network.
        for legacy, current in {
            "epn_hdc": "europe_epn",
            "epn_rob": "europe_epn",
            "epn_bev": "europe_epn",
            "epn_bkg": "europe_epn",
            "epn_bkge": "europe_epn",
            "epn_bkgi": "europe_epn",
            "epn_ign": "europe_epn",
            "ROB / EPN": "europe_epn",
            "BEV": "europe_epn",
            "BKG": "europe_epn",
            "IGN": "europe_epn",
            "ergnss_es": "europe_redgae",
        }.items():
            if current in self._sources:
                self._aliases[_source_key(legacy)] = current

    def all(self, data_network: str | None = None) -> list[RegionalSource]:
        if data_network is None:
            return list(self._sources.values())
        key = data_network.lower().replace("-", "_")
        return [source for source in self._sources.values() if source.data_network == key]

    def get(self, value: str) -> RegionalSource:
        source_id = self.normalize(value)
        try:
            return self._sources[source_id]
        except KeyError as exc:
            available = ", ".join(source.name for source in self.all())
            raise ConfigurationError(
                f"Unknown regional source {value!r}. Available: {available}."
            ) from exc

    def normalize(self, value: str) -> str:
        key = _source_key(value)
        try:
            return self._aliases[key]
        except KeyError as exc:
            available = ", ".join(source.name for source in self.all())
            raise ConfigurationError(
                f"Unknown regional source {value!r}. Available: {available}."
            ) from exc

    def contains(self, value: str, *, data_network: str | None = None) -> bool:
        try:
            source = self.get(value)
        except ConfigurationError:
            return False
        return data_network is None or source.data_network == data_network.lower().replace("-", "_")

    def normalize_many(
        self,
        values: list[str] | tuple[str, ...] | None,
        *,
        data_network: str | None = None,
    ) -> list[str]:
        normalized = []
        for value in values or []:
            source = self.get(value)
            if data_network and source.data_network != data_network.lower().replace("-", "_"):
                raise ConfigurationError(f"{source.name} is not a source of {data_network}.")
            normalized.append(source.id)
        return sorted(set(normalized))


def australia_sources() -> list[RegionalSource]:
    return [
        RegionalSource("auscope", "AUSCOPE", "australia", "ga"),
        RegionalSource("corsnet_nsw", "CORSNET-NSW", "australia", "ga"),
        RegionalSource("gpsnet", "GPSNET", "australia", "ga"),
        RegionalSource("rtknetwest", "RTKNETWEST", "australia", "ga"),
        RegionalSource("sunpoz", "SUNPOZ", "australia", "ga"),
        RegionalSource("ntcors", "NTCORS", "australia", "ga"),
        RegionalSource("qld_tmr", "QLD_TMR", "australia", "ga"),
        RegionalSource("ips", "IPS", "australia", "ga"),
        RegionalSource("upg", "UPG", "australia", "ga"),
        RegionalSource("rps", "RPS", "australia", "ga"),
        RegionalSource("smartnet", "SMARTNET", "australia", "ga"),
    ]



def europe_sources() -> list[RegionalSource]:
    # Second-level Europe choices are logical station networks, not file servers.
    # EPN keeps ROB/BEV/BKG/IGN internally as mirrors/fallbacks.
    return [
        RegionalSource("europe_epn", "EPN", "europe", "epn"),
        RegionalSource("europe_rgp", "France · RGP", "europe", "rgp_fr"),
        RegionalSource("europe_gref", "Germany · GREF", "europe", "gref_de"),
        RegionalSource("europe_redgae", "Spain · redGAE", "europe", "redgae_es"),
        RegionalSource("europe_nsgi", "Netherlands · AGRS/NETPOS", "europe", "nsgi_nl"),
        RegionalSource("europe_apos", "Austria · APOS", "europe", "apos_at"),
        RegionalSource("europe_renep", "Portugal · ReNEP", "europe", "renep_pt"),
        RegionalSource("europe_belgium", "Belgium · GNSS.be", "europe", "belgium_be"),
        RegionalSource("europe_greece", "Greece · NOA/EPOS", "europe", "noa_gr"),
        RegionalSource("europe_italy", "Italy · EPOS/GLASS (RING + others)", "europe", "epos_it"),
        RegionalSource("europe_poland", "Poland · EPOS/GLASS (ASG-EUPOS)", "europe", "epos_pl"),
        RegionalSource("europe_romania", "Romania · EPOS National Node", "europe", "epos_ro"),
        RegionalSource("europe_uk", "United Kingdom · EPOS/GLASS (OS Net)", "europe", "epos_uk"),
        RegionalSource("europe_sweden", "Sweden · EPOS/GLASS (SWEPOS)", "europe", "epos_se"),
        RegionalSource("europe_finland", "Finland · EPOS/GLASS (FinnRef/FINPOS)", "europe", "epos_fi"),
        RegionalSource("europe_switzerland", "Switzerland · EPOS/GLASS (AGNES)", "europe", "epos_ch"),
        RegionalSource("europe_hungary", "Hungary · EPOS/GLASS", "europe", "epos_hu"),
        RegionalSource("europe_czechia", "Czechia · EPOS/GLASS", "europe", "epos_cz"),
        RegionalSource("europe_slovenia", "Slovenia · EPOS/GLASS", "europe", "epos_si"),
        RegionalSource("europe_ireland", "Ireland · EPOS/GLASS", "europe", "epos_ie"),
        RegionalSource("europe_iceland", "Iceland · EPOS/GLASS", "europe", "epos_is"),
        RegionalSource("europe_croatia", "Croatia · EPOS/GLASS", "europe", "epos_hr"),
        RegionalSource("europe_norway", "Norway · EPOS/GLASS", "europe", "epos_no"),
        RegionalSource("europe_denmark", "Denmark · EPOS/GLASS", "europe", "epos_dk"),
        RegionalSource("europe_estonia", "Estonia · EPOS/GLASS", "europe", "epos_ee"),
        RegionalSource("europe_latvia", "Latvia · EPOS/GLASS", "europe", "epos_lv"),
        RegionalSource("europe_lithuania", "Lithuania · EPOS/GLASS", "europe", "epos_lt"),
        RegionalSource("europe_slovakia", "Slovakia · EPOS/GLASS", "europe", "epos_sk"),
        RegionalSource("europe_bulgaria", "Bulgaria · EPOS/GLASS", "europe", "epos_bg"),
        RegionalSource("europe_cyprus", "Cyprus · EPOS/GLASS", "europe", "epos_cy"),
        RegionalSource("europe_serbia", "Serbia · EPOS/GLASS", "europe", "epos_rs"),
        RegionalSource("europe_turkey", "Türkiye · EPOS/GLASS", "europe", "epos_tr"),
        RegionalSource("europe_luxembourg", "Luxembourg · EPOS/GLASS", "europe", "epos_lu"),
        RegionalSource("europe_albania", "Albania · EPOS/GLASS", "europe", "epos_al"),
        RegionalSource("europe_bosnia", "Bosnia and Herzegovina · EPOS/GLASS", "europe", "epos_ba"),
        RegionalSource("europe_north_macedonia", "North Macedonia · EPOS/GLASS", "europe", "epos_mk"),
        RegionalSource("europe_moldova", "Moldova · EPOS/GLASS", "europe", "epos_md"),
        RegionalSource("europe_ukraine", "Ukraine · EPOS/GLASS", "europe", "epos_ua"),
        RegionalSource("europe_malta", "Malta · EPOS/GLASS", "europe", "epos_mt"),
        RegionalSource("europe_montenegro", "Montenegro · EPOS/GLASS", "europe", "epos_me"),
    ]


def sirgas_sources() -> list[RegionalSource]:
    return [
        RegionalSource("sirgas_argentina", "Argentina · RAMSAC", "sirgas", "ramsac_ar"),
        RegionalSource("sirgas_brazil", "Brazil · RBMC", "sirgas", "sirgas_rbmc_br"),
        RegionalSource("sirgas_chile", "Chile · CSN", "sirgas", "sirgas_cl"),
        RegionalSource("sirgas_mexico", "Mexico · INEGI RGNA", "sirgas", "rgna_mx"),
        RegionalSource("sirgas_bolivia", "Bolivia · IGM / SIRGAS", "sirgas", "sirgas_bo"),
        RegionalSource("sirgas_colombia", "Colombia · IGAC / SIRGAS", "sirgas", "sirgas_co"),
        RegionalSource("sirgas_ecuador", "Ecuador · IGM / SIRGAS", "sirgas", "sirgas_ec"),
        RegionalSource("sirgas_peru", "Peru · IGN / SIRGAS", "sirgas", "sirgas_pe"),
        RegionalSource("sirgas_uruguay", "Uruguay · IGM REGNA-ROU", "sirgas", "sirgas_uy"),
        RegionalSource("sirgas_costa_rica", "Costa Rica · IGN / SIRGAS", "sirgas", "sirgas_cr"),
        RegionalSource("sirgas_panama", "Panama · IGNTG / SIRGAS", "sirgas", "sirgas_pa"),
    ]


def canada_sources() -> list[RegionalSource]:
    return [
        RegionalSource("cacs_ca", "NRCan CACS", "canada", "cacs_ca"),
        RegionalSource("chain_ca", "UNB CHAIN", "canada", "chain_ca"),
    ]



def asia_sources() -> list[RegionalSource]:
    return [
        RegionalSource("japan_geonet", "GSI GEONET (Terras)", "japan", "geonet_jp"),
        RegionalSource("china_cmonoc", "China · CMONOC", "china", "cmonoc_cn"),
        RegionalSource("taiwan_gdms", "Taiwan, China · GDMS", "taiwan", "gdms_tw"),
        RegionalSource("hongkong_satref", "Hong Kong, China · SatRef", "hong_kong", "satref_hk"),
        RegionalSource("mongolia_monpos", "MONPOS", "mongolia", "monpos_mn"),
        RegionalSource("korea_kasi", "KASI KASINet / KVN FTP", "korea", "kasi_kr"),
        RegionalSource("korea_national", "National GNSS Data Center", "korea", "ngii_kr"),
        RegionalSource("singapore_sirent", "SiReNT", "singapore", "sirent_sg"),
    ]


def oceania_sources() -> list[RegionalSource]:
    return [
        RegionalSource("newzealand_geonet", "GeoNet New Zealand", "new_zealand", "geonet_nz"),
    ]


def north_america_sources() -> list[RegionalSource]:
    return [
        RegionalSource("usa_noaa_cors", "NOAA National CORS Network", "united_states", "noaa_ncn"),
    ]


def africa_sources() -> list[RegionalSource]:
    return [
        RegionalSource("southafrica_trignet", "TrigNet", "south_africa", "trignet_za"),
    ]

def default_sources() -> list[RegionalSource]:
    return [
        *australia_sources(),
        *europe_sources(),
        *sirgas_sources(),
        *canada_sources(),
        *asia_sources(),
        *oceania_sources(),
        *north_america_sources(),
        *africa_sources(),
    ]

def default_regional_source_registry() -> RegionalSourceRegistry:
    return RegionalSourceRegistry()


def normalize_regional_source(value: str) -> str:
    return default_regional_source_registry().normalize(value)


def source_display_name(source_id: str) -> str:
    return default_regional_source_registry().get(source_id).name


def _source_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
