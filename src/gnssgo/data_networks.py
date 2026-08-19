from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from gnssgo.exceptions import ConfigurationError


class AutomationLevel(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    AUTH_REQUIRED = "auth_required"
    INTERACTIVE_WEB = "interactive_web"
    BROWSER_REQUIRED = "browser_required"
    MANUAL = "manual"
    UNVERIFIED = "unverified"


class AccessStrategy(StrEnum):
    HTTP_DIRECT = "http_direct"
    HTTPS_DIRECT = "https_direct"
    DIRECTORY_LISTING = "directory_listing"
    REST_API = "rest_api"
    JSON_API = "json_api"
    S3_PUBLIC = "s3_public"
    FTP = "ftp"
    FTPS = "ftps"
    SFTP = "sftp"
    AUTH_HTTP = "auth_http"
    INTERACTIVE_WEB = "interactive_web"
    BROWSER_AUTOMATION = "browser_automation"


@dataclass(frozen=True)
class DataNetwork:
    id: str
    name: str
    category: str
    providers: list[str]
    countries: list[str] | None = None
    description: str | None = None
    automation_level: AutomationLevel = AutomationLevel.UNVERIFIED
    access_strategies: list[AccessStrategy] = field(default_factory=list)
    sampling: list[str] = field(default_factory=lambda: ["30S"])
    status: str = "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED"


class DataNetworkRegistry:
    def __init__(self, networks: list[DataNetwork] | None = None) -> None:
        self._networks = {network.id: network for network in networks or default_networks()}

    def get(self, network_id: str) -> DataNetwork:
        key = network_id.lower().replace("-", "_")
        try:
            return self._networks[key]
        except KeyError as exc:
            available = ", ".join(sorted(self._networks))
            raise ConfigurationError(
                f"Unknown data network {network_id!r}. Available: {available}."
            ) from exc

    def all(self) -> list[DataNetwork]:
        return list(self._networks.values())

    def global_networks(self) -> list[DataNetwork]:
        return [network for network in self.all() if network.category == "global"]

    def regional_networks(self) -> list[DataNetwork]:
        return [network for network in self.all() if network.category == "regional"]

    def providers_for(self, network_ids: list[str]) -> list[str]:
        providers: list[str] = []
        seen: set[str] = set()
        for network_id in network_ids:
            for provider in self.get(network_id).providers:
                if provider in seen:
                    continue
                seen.add(provider)
                providers.append(provider)
        return providers


def default_networks() -> list[DataNetwork]:
    return [
        DataNetwork(
            id="igs",
            name="IGS",
            category="global",
            providers=[
                "whu",
                "kasi",
                "esa",
                "ign",
                "sopac",
                "bdsmart",
                "bkgftp",
                "bkg",
                "noaa",
            ],
            description="International GNSS Service global station network.",
            automation_level=AutomationLevel.FULL,
            access_strategies=[AccessStrategy.FTP, AccessStrategy.HTTPS_DIRECT],
            status="FULLY_AUTOMATED + LIVE_VERIFIED",
        ),
        _regional("japan", "Japan", ["geonet_jp"], ["JPN"], AutomationLevel.BROWSER_REQUIRED, sampling=["30S"]),
        _regional("china", "China", ["cmonoc_cn"], ["CHN"], AutomationLevel.MANUAL, sampling=["30S"]),
        _regional("taiwan", "Taiwan, China", ["gdms_tw"], ["TWN"], AutomationLevel.AUTH_REQUIRED, sampling=["30S"]),
        _regional("australia", "Australia", ["ga"], ["AUS"], AutomationLevel.PARTIAL),
        _regional(
            "new_zealand",
            "New Zealand",
            ["geonet_nz"],
            ["NZL"],
            AutomationLevel.FULL,
        ),
        _regional(
            "sirgas",
            "SIRGAS / Latin America",
            [
                "ramsac_ar", "sirgas_rbmc_br", "sirgas_cl", "rgna_mx",
                "sirgas_bo", "sirgas_co", "sirgas_ec", "sirgas_pe",
                "sirgas_uy", "sirgas_cr", "sirgas_pa",
            ],
            None,
            AutomationLevel.PARTIAL,
            sampling=["01S", "15S", "30S"],
        ),
        _regional(
            "europe",
            "Europe",
            [
                "epn", "rgp_fr", "gref_de", "redgae_es", "nsgi_nl", "apos_at", "renep_pt",
                "belgium_be", "noa_gr", "epos_it", "epos_pl", "epos_ro", "epos_uk", "epos_se",
                "epos_fi", "epos_ch", "epos_hu", "epos_cz", "epos_si", "epos_ie", "epos_is",
                "epos_hr", "epos_no", "epos_dk", "epos_ee", "epos_lv", "epos_lt", "epos_sk",
                "epos_bg", "epos_cy", "epos_rs", "epos_tr", "epos_lu", "epos_al", "epos_ba",
                "epos_mk", "epos_md", "epos_ua", "epos_mt", "epos_me",
            ],
            None,
            AutomationLevel.PARTIAL,
        ),
        _regional(
            "netherlands",
            "Netherlands",
            ["nsgi_nl", "dpga_nl"],
            ["NLD"],
            AutomationLevel.PARTIAL,
            sampling=["01S", "10S", "30S"],
        ),
        _regional("italy", "Italy", ["epos_it"], ["ITA"], AutomationLevel.PARTIAL),
        _regional(
            "brazil",
            "Brazil",
            ["rbmc_br"],
            ["BRA"],
            AutomationLevel.FULL,
            sampling=["01S", "15S", "30S"],
        ),
        _regional(
            "canada",
            "Canada",
            ["cacs_ca", "chain_ca"],
            ["CAN"],
            AutomationLevel.UNVERIFIED,
            sampling=["01S", "30S"],
        ),
        _regional(
            "united_kingdom",
            "United Kingdom",
            ["epos_uk", "osnet_uk"],
            ["GBR"],
            AutomationLevel.PARTIAL,
        ),
        _regional("poland", "Poland", ["epos_pl"], ["POL"], AutomationLevel.PARTIAL),
        _regional("romania", "Romania", ["epos_ro"], ["ROU"], AutomationLevel.PARTIAL),
        _regional("sweden", "Sweden", ["epos_se"], ["SWE"], AutomationLevel.PARTIAL),
        _regional("finland", "Finland", ["epos_fi"], ["FIN"], AutomationLevel.PARTIAL),
        _regional("switzerland", "Switzerland", ["epos_ch"], ["CHE"], AutomationLevel.PARTIAL),
        _regional(
            "france",
            "France",
            ["renag_fr", "rgp_fr"],
            ["FRA"],
            AutomationLevel.UNVERIFIED,
            sampling=["01S", "30S"],
        ),
        _regional("spain", "Spain", ["redgae_es"], ["ESP"], AutomationLevel.PARTIAL),
        _regional(
            "hong_kong",
            "Hong Kong, China",
            ["satref_hk"],
            ["HKG"],
            AutomationLevel.UNVERIFIED,
            sampling=["01S", "05S", "30S"],
        ),
        _regional("mongolia", "Mongolia", ["monpos_mn"], ["MNG"], AutomationLevel.AUTH_REQUIRED),
        _regional("argentina", "Argentina", ["ramsac_ar"], ["ARG"], AutomationLevel.PARTIAL),
        _regional(
            "mexico",
            "Mexico",
            ["rgna_mx"],
            ["MEX"],
            AutomationLevel.FULL,
            sampling=["15S", "30S"],
        ),
        _regional(
            "south_africa",
            "South Africa",
            ["trignet_za"],
            ["ZAF"],
            AutomationLevel.UNVERIFIED,
            sampling=["01S", "30S"],
        ),
        _regional("portugal", "Portugal", ["renep_pt"], ["PRT"], AutomationLevel.PARTIAL),
        _regional(
            "austria",
            "Austria",
            ["apos_at"],
            ["AUT"],
            AutomationLevel.INTERACTIVE_WEB,
            sampling=["01S", "30S"],
        ),
        _regional(
            "korea",
            "Korea",
            ["kasi_kr", "ngii_kr"],
            ["KOR"],
            AutomationLevel.FULL,
            sampling=["30S"],
        ),
        _regional("singapore", "Singapore", ["sirent_sg"], ["SGP"], AutomationLevel.AUTH_REQUIRED),
        _regional(
            "united_states",
            "United States",
            ["noaa_ncn"],
            ["USA"],
            AutomationLevel.FULL,
            sampling=["01S", "05S", "15S", "30S"],
        ),
        _regional(
            "north_america",
            "North America",
            ["earthscope_us"],
            ["USA", "CAN"],
            AutomationLevel.UNVERIFIED,
        ),
    ]


def _regional(
    network_id: str,
    name: str,
    providers: list[str],
    countries: list[str] | None,
    automation_level: AutomationLevel,
    sampling: list[str] | None = None,
) -> DataNetwork:
    status_by_level = {
        AutomationLevel.FULL: "FULLY_AUTOMATED + LIVE_VERIFIED",
        AutomationLevel.PARTIAL: "PARTIALLY_AUTOMATED + LIVE_VERIFIED",
        AutomationLevel.AUTH_REQUIRED: "AUTH_REQUIRED",
        AutomationLevel.INTERACTIVE_WEB: "INTERACTIVE_WEB",
        AutomationLevel.BROWSER_REQUIRED: "BROWSER_REQUIRED",
        AutomationLevel.MANUAL: "MANUAL",
        AutomationLevel.UNVERIFIED: "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED",
    }
    return DataNetwork(
        id=network_id,
        name=name,
        category="regional",
        providers=providers,
        countries=countries,
        automation_level=automation_level,
        access_strategies=[AccessStrategy.INTERACTIVE_WEB]
        if automation_level
        in {
            AutomationLevel.BROWSER_REQUIRED,
            AutomationLevel.INTERACTIVE_WEB,
            AutomationLevel.AUTH_REQUIRED,
        }
        else [],
        status=status_by_level[automation_level],
        sampling=sampling or ["30S"],
    )


def default_data_network_registry() -> DataNetworkRegistry:
    return DataNetworkRegistry()
