from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    url: str
    access: str = ""
    description: str = ""


# User-facing metadata only. Download/discovery endpoints remain owned by each provider.
_PROVIDER_INFO: dict[str, ProviderInfo] = {
    "whu": ProviderInfo(
        "whu", "Wuhan University IGS Data Center", "https://www.igs.gnsswhu.cn/", "FTP / HTTPS"
    ),
    "kasi": ProviderInfo("kasi", "KASI GNSS Data Center", "ftp://nfs.kasi.re.kr/gps", "FTP"),
    "esa": ProviderInfo(
        "esa", "ESA GNSS Science Support Centre", "https://gssc.esa.int/", "FTP / Web"
    ),
    "ign": ProviderInfo("ign", "IGN IGS Data Center", "https://igs.ign.fr/", "FTP"),
    "sopac": ProviderInfo("sopac", "SOPAC / UCSD", "https://garner.ucsd.edu/pub", "HTTPS"),
    "bdsmart": ProviderInfo(
        "bdsmart", "BDSmart IGS Archive", "https://data.bdsmart.cn/pub/data/igs", "HTTPS"
    ),
    "bkgftp": ProviderInfo("bkgftp", "BKG IGS FTP", "https://igs.bkg.bund.de/", "FTP"),
    "bkg": ProviderInfo("bkg", "BKG IGS Data Center", "https://igs.bkg.bund.de/", "HTTPS"),
    "noaa": ProviderInfo("noaa", "NOAA CORS Network", "https://www.ngs.noaa.gov/CORS/", "HTTPS"),
    "igsfiles": ProviderInfo(
        "igsfiles",
        "IGS Central Bureau Files",
        "https://files.igs.org/pub/station/general/",
        "Public HTTPS",
        "Current IGS ANTEX and station SINEX files.",
    ),
    "ga": ProviderInfo(
        "ga",
        "Geoscience Australia GNSS Data Repository",
        "https://data.gnss.ga.gov.au/",
        "API / S3",
    ),
    "epn": ProviderInfo(
        "epn",
        "EUREF Permanent GNSS Network",
        "https://gnss.be/epndata.php",
        "ROB/EPN API + BEV/BKG/IGN HTTPS",
    ),
    "belgium_be": ProviderInfo(
        "belgium_be",
        "ROB Belgian GNSS repository",
        "https://gnss.be/belgiandata.php",
        "Public GNSS.be API / HTTPS",
        "Belgian EPOS-GNSS daily RINEX repository; files are served under gnss.be/pub/RINEX.",
    ),
    "noa_gr": ProviderInfo(
        "noa_gr",
        "Greece NOA / EPOS-GNSS",
        "https://www.gein.noa.gr/services/GPSData/",
        "NOA HTTPS / EPOS GLASS fallback",
        "Greek daily RINEX is downloaded directly from the NOA/GEIN GPSData archive; EPOS GLASS supplies structured metadata and fallback discovery.",
    ),
    "geonet_nz": ProviderInfo(
        "geonet_nz",
        "GeoNet New Zealand",
        "https://data.geonet.org.nz/gnss/",
        "Public HTTPS / AWS",
    ),
    "rbmc_br": ProviderInfo(
        "rbmc_br",
        "IBGE RBMC",
        (
            "https://www.ibge.gov.br/geociencias/informacoes-sobre-posicionamento-geodesico/"
            "rede-geodesica/16258-rede-brasileira-de-monitoramento-continuo-dos-sistemas-"
            "gnss-rbmc.html"
        ),
        "HTTPS",
    ),
    "nsgi_nl": ProviderInfo(
        "nsgi_nl",
        "Kadaster / NSGI AGRS.NL + NETPOS",
        "https://gnss-data.kadaster.nl/",
        "Public HTTPS",
        "Official Dutch GNSS data centre with daily RINEX and current station metadata.",
    ),
    "apos_at": ProviderInfo(
        "apos_at",
        "BEV Austria APOS",
        "https://data.bev.gv.at/",
        "Free Geoportal / interactive",
        "APOS-PP RINEX is free and registration-free; direct file API is not publicly documented, so GNSS Go exposes the official catalog without guessing URLs.",
    ),
    "dpga_nl": ProviderInfo(
        "dpga_nl",
        "TU Delft DPGA",
        "https://gnss1.tudelft.nl/dpga/",
        "Public HTTPS / anonymous FTP",
        "Dutch Permanent GNSS Array daily, hourly, and high-rate RINEX archive.",
    ),
    "ring_it": ProviderInfo(
        "ring_it",
        "INGV RING",
        "https://webring.gm.ingv.it:44324/",
        "HTTPS / Web",
        (
            "RING/Mediterranean GNSS RINEX and products archive; "
            "exact automated layout not live verified."
        ),
    ),
    "chain_ca": ProviderInfo(
        "chain_ca",
        "UNB CHAIN",
        "https://chain-new.chain-project.net/index.php/data-products/data-download",
        "Public HTTPS / FTP",
        "Independent Canadian High Arctic network. The public web archive exposes "
        "multi-constellation RINEX 3.03 under /data/gnss and legacy GPS RINEX 2.11 "
        "under /data/gps, with daily 30-second and high-rate 1-second data. "
        "FTP is also available from ftp.chain-project.net.",
    ),
    "wcda_ca": ProviderInfo(
        "wcda_ca",
        "NRCan WCDA (internal/legacy endpoint)",
        "https://wcda2.nrcan.gc.ca/",
        "Internal / legacy",
        "WCDA is not exposed as a separate Canada source in the GUI. CACS/CGS distributes "
        "Canadian GNSS data, including contributed regional networks; IGS mirrors remain the "
        "same-station fallback for stations available globally.",
    ),
    "renag_fr": ProviderInfo(
        "renag_fr",
        "Rénag / EPOS-France",
        "https://renag.epos-france.fr/donnees/",
        "Public HTTPS / FTP",
        "RINEX 2/3 archive with 30-second data and 1-second data for supported stations.",
    ),
    "kasi_kr": ProviderInfo(
        "kasi_kr",
        "KASI KASINet / KVN",
        "https://gnss.kasi.re.kr/gnss_download.php",
        "Anonymous FTP / Web",
        (
            "Automatic Korean regional download source. GNSS Go checks both anonymous FTP "
            "archives under /kasinet/daily and /kvn/daily for each requested day, and supports "
            "legacy short-name Hatanaka files plus long-name RINEX 3 compact observation files."
        ),
    ),
    "monpos_mn": ProviderInfo(
        "monpos_mn",
        "Mongolia MONPOS",
        "https://monpos.gazar.gov.mn/download/",
        "Account / Web",
        (
            "MONPOS static CORS data portal; user registration/login is required "
            "for the download page."
        ),
    ),
    "noaa_ncn": ProviderInfo(
        "noaa_ncn",
        "NOAA National CORS Network",
        "https://geodesy.noaa.gov/CORS/data.shtml",
        "Public HTTPS / AWS",
        (
            "US NOAA/NGS CORS. Station locations and status are loaded from the bundled NOAA "
            "CORS catalog exported from the official map; daily RINEX uses NOAA Open Data on "
            "AWS with the NGS HTTPS archive as fallback."
        ),
    ),
    "sirgas_py": ProviderInfo("sirgas_py", "Paraguay SIRGAS", "https://sirgas.ipgh.org/en/gnss-network/data-centres/", "SIRGAS / national source"),
    "sirgas_ve": ProviderInfo("sirgas_ve", "Venezuela SIRGAS", "https://sirgas.ipgh.org/en/gnss-network/data-centres/", "SIRGAS / national source"),
    "sirgas_gy": ProviderInfo("sirgas_gy", "Guyana SIRGAS", "https://sirgas.ipgh.org/en/gnss-network/data-centres/", "SIRGAS / national source"),
    "sirgas_sr": ProviderInfo("sirgas_sr", "Suriname SIRGAS", "https://sirgas.ipgh.org/en/gnss-network/data-centres/", "SIRGAS / national source"),
    "gdms_tw": ProviderInfo(
        "gdms_tw",
        "Taiwan, China CWA GDMS",
        "https://gdms.cwa.gov.tw/GeophyDownload.php",
        "Registered account / interactive web",
        "Official Taiwan, China Seismological and Geophysical Data Management System GNSS download portal. The station map/catalog is published at https://gdms.cwa.gov.tw/map.php. GNSS downloads require login; the portal states a maximum 7-day selection and additional availability rules for GNSS_IES/GNSS_ETEC.",
    ),
    "cmonoc_cn": ProviderInfo(
        "cmonoc_cn",
        "China CMONOC / National Earthquake Science Data Center",
        "https://data.earthquake.cn/datashare/report.shtml?PAGEID=siteInfo_jizhun",
        "Official station-information portal",
        "China Mainland Crustal Movement Observation Network (CMONOC) benchmark-station metadata. GNSS Go indexes the official station catalog and links to the source page; no undocumented machine RINEX endpoint is assumed.",
    ),
    "geonet_jp": ProviderInfo(
        "geonet_jp", "GSI GEONET", "https://terras.gsi.go.jp/data_service.php", "Terras Web / SFTP"
    ),
    "cacs_ca": ProviderInfo(
        "cacs_ca",
        "NRCan CACS / CACSA",
        "https://cacsa.nrcan.gc.ca/",
        "Public HTTPS archive / Web",
        (
            "Primary Canadian Active Control System source. The public CACSA 30-second daily "
            "archive is read directly from YYDDD/YYd directories and may contain both RINEX 3/4 "
            "long-name MO files and legacy RINEX 2 d files. GNSS Go prefers RINEX 3/4 in "
            "Auto mode and uses RINEX 2 as the same-station fallback. The CACS web service "
            "also advertises 1-second observations, but the high-rate archive layout is not "
            "guessed until independently verified."
        ),
    ),
    "osnet_uk": ProviderInfo(
        "osnet_uk",
        "Ordnance Survey OS Net",
        "https://www.ordnancesurvey.co.uk/geodesy-positioning/os-net",
        "Account",
    ),
    "rgp_fr": ProviderInfo(
        "rgp_fr",
        "IGN France RGP",
        "https://rgp.ign.fr/",
        "Public HTTPS / FTP",
        "French permanent GNSS network; daily RINEX is served by rgpdata.ign.fr.",
    ),
    "gref_de": ProviderInfo(
        "gref_de",
        "BKG Germany GREF",
        "https://gref.bkg.bund.de/",
        "Public HTTPS",
        "German national geodetic reference network; RINEX is distributed by BKG GDC.",
    ),
    "redgae_es": ProviderInfo(
        "redgae_es",
        "IGN Spain redGAE",
        "https://redgae.ign.es/",
        "Public HTTPS / federated portals",
        "Spanish public GNSS network federation. GNSS Go automates the central ERGNSS daily archive and indexes all redGAE stations.",
    ),
    "ergnss_es": ProviderInfo(
        "ergnss_es",
        "IGN Spain ERGNSS",
        "https://www.ign.es/web/gds-gnss-estaciones-permanentes",
        "Web",
    ),
    "satref_hk": ProviderInfo(
        "satref_hk",
        "Hong Kong, China SatRef",
        "https://rinex.geodetic.gov.hk/",
        "Public HTTPS / FTPS",
        "SatRef RINEX 2/3 data at 1-second, 5-second, and 30-second intervals.",
    ),
    "ramsac_ar": ProviderInfo(
        "ramsac_ar",
        "IGN Argentina RAMSAC",
        "https://www.ign.gob.ar/NuestrasActividades/Geodesia/Ramsac/DescargaRinex",
        "Official station catalog / interactive RINEX portal",
        "RAMSAC stations are indexed automatically. GNSS Go does not invent an undocumented download URL; the official filtered RINEX portal is used when direct machine access is unavailable.",
    ),
    "sirgas_rbmc_br": ProviderInfo(
        "sirgas_rbmc_br", "Brazil IBGE RBMC (SIRGAS)",
        "https://geoftp.ibge.gov.br/informacoes_sobre_posicionamento_geodesico/rbmc/",
        "Public HTTPS", "Direct IBGE RBMC RINEX archive exposed inside the SIRGAS / Latin America group."
    ),
    "sirgas_cl": ProviderInfo(
        "sirgas_cl", "Chile CSN", "https://gps.csn.uchile.cl/data/",
        "Public HTTPS", "CSN publishes daily compact/Hatanaka RINEX at 1 Hz. The YYYY/DOY directory is authoritative for file availability; the bundled CSN KML supplies map membership/coordinates."
    ),
    "rgna_mx": ProviderInfo(
        "rgna_mx", "Mexico INEGI RGNA", "https://www.inegi.org.mx/app/geo2/rgna/",
        "Public SFTP", "INEGI is authoritative for station metadata. PLAN uses the documented /home/rgna station tree and deterministic hourly ZIP naming; the SFTP connection is opened only during download so PLAN cannot block on SSH."
    ),
    "sirgas_bo": ProviderInfo(
        "sirgas_bo", "Bolivia IGM MARGEN-ROC", "https://cposirgasbol.igmbolivia.gob.bo/",
        "Official download portal", "Bolivian continuous GNSS raw-data download system; automated machine endpoint not yet documented."
    ),
    "sirgas_co": ProviderInfo(
        "sirgas_co", "Colombia IGAC MAGNA-ECO", "https://www.colombiaenmapas.gov.co/?b=igac&u=0&t=25&servicio=6",
        "Public web portal", "IGAC publishes daily RINEX through Colombia en Mapas; GNSS Go currently indexes the SIRGAS subset while a stable machine endpoint is being verified."
    ),
    "sirgas_ec": ProviderInfo(
        "sirgas_ec", "Ecuador IGM REGME", "https://www.geoportaligm.gob.ec/portal_geodesia/",
        "Registration / Geoportal", "REGME daily GNSS data are distributed through the IGM Geoportal; account/terms workflow is not automated."
    ),
    "sirgas_pe": ProviderInfo(
        "sirgas_pe", "Peru IGN REGPMOC", "https://www.gob.pe/ign",
        "Official portal", "Peruvian continuous GNSS network; GNSS Go currently indexes its SIRGAS stations pending a stable public file endpoint."
    ),
    "sirgas_uy": ProviderInfo(
        "sirgas_uy", "Uruguay IGM REGNA-ROU", "https://igm.gub.uy/2016/05/20/servicios-regna-rou/",
        "Anonymous FTP / public historical SFTP", "Current-year daily Hatanaka/RINEX files are read from /hatanaka/YYYY/DOY on pp.igm.gub.uy (including short names such as uyar0180.26d.gz and long RINEX-3 names). Historical 30 s uses /sftpserver/YYYY/MM/DD/STATION and 1 s uses /sftpserver/YYYY/MM/DD/0/STATION on the public SFTP archive."
    ),
    "sirgas_cr": ProviderInfo(
        "sirgas_cr", "Costa Rica IGN GNSS", "https://gnss.rnp.go.cr/SBC/spider-business-center",
        "Account / portal", "Registro Nacional provides RINEX through Spider Business Center; account activation is required."
    ),
    "sirgas_pa": ProviderInfo(
        "sirgas_pa", "Panama IGNTG CORS", "https://ignpanama.anati.gob.pa/index.php/cors?sigplus=14",
        "Official portal", "PORTAL: cors.anati.gob.pa currently times out in direct SFTP client testing, so GNSS Go no longer advertises it as LIVE until a working machine endpoint is verified."
    ),
    "epos_it": ProviderInfo("epos_it", "Italy EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_pl": ProviderInfo("epos_pl", "Poland EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_ro": ProviderInfo("epos_ro", "Romania EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_uk": ProviderInfo("epos_uk", "United Kingdom EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_se": ProviderInfo("epos_se", "Sweden EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_fi": ProviderInfo("epos_fi", "Finland EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_ch": ProviderInfo("epos_ch", "Switzerland EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_hu": ProviderInfo("epos_hu", "Hungary EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_cz": ProviderInfo("epos_cz", "Czechia EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_si": ProviderInfo("epos_si", "Slovenia EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_ie": ProviderInfo("epos_ie", "Ireland EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_is": ProviderInfo("epos_is", "Iceland EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_hr": ProviderInfo("epos_hr", "Croatia EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_no": ProviderInfo("epos_no", "Norway EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_dk": ProviderInfo("epos_dk", "Denmark EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_ee": ProviderInfo("epos_ee", "Estonia EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_lv": ProviderInfo("epos_lv", "Latvia EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_lt": ProviderInfo("epos_lt", "Lithuania EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_sk": ProviderInfo("epos_sk", "Slovakia EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_bg": ProviderInfo("epos_bg", "Bulgaria EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_cy": ProviderInfo("epos_cy", "Cyprus EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_rs": ProviderInfo("epos_rs", "Serbia EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_tr": ProviderInfo("epos_tr", "Türkiye EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_lu": ProviderInfo("epos_lu", "Luxembourg EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_al": ProviderInfo("epos_al", "Albania EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_ba": ProviderInfo("epos_ba", "Bosnia and Herzegovina EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_mk": ProviderInfo("epos_mk", "North Macedonia EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_md": ProviderInfo("epos_md", "Moldova EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_ua": ProviderInfo("epos_ua", "Ukraine EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_mt": ProviderInfo("epos_mt", "Malta EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "epos_me": ProviderInfo("epos_me", "Montenegro EPOS/GLASS", "https://gnssdata-epos.oca.eu/", "EPOS GLASS API"),
    "trignet_za": ProviderInfo(
        "trignet_za",
        "South Africa TrigNet",
        "https://www.trignet.co.za/",
        "FTP / Web",
        (
            "TrigNet RINEX 3 archive; official site lists 1-second/30-second daily "
            "and 1-second hourly data."
        ),
    ),
    "renep_pt": ProviderInfo(
        "renep_pt",
        "Portugal ReNEP",
        "https://glass.epos.ubi.pt/#/file/",
        "EPOS GLASS API / HTTPS",
        "Portuguese ReNEP station metadata and RINEX file URLs are discovered through EPOS GLASS, matching the pyglass distribution workflow.",
    ),
    "ngii_kr": ProviderInfo(
        "ngii_kr",
        "Korea National GNSS Data Center",
        "https://www.gnssdata.or.kr/download/getDownloadView.do",
        "Automatic HTTPS session ZIP",
        (
            "National Korean CORS catalog plus the verified public GNSSData web-session "
            "download workflow. GNSS Go creates a temporary ZIP key at download time and "
            "immediately retrieves it in the same JSESSIONID; no OpenAPI/File-Key is required."
        ),
    ),
    "sirent_sg": ProviderInfo(
        "sirent_sg", "Singapore SiReNT", "https://app.sla.gov.sg/sirent/", "Account"
    ),
    "earthscope_us": ProviderInfo(
        "earthscope_us", "EarthScope / GAGE", "https://www.earthscope.org/data/", "Public Web"
    ),
}


def provider_info(provider_id: str) -> ProviderInfo:
    key = provider_id.lower()
    return _PROVIDER_INFO.get(key, ProviderInfo(key, provider_id.upper(), ""))


def provider_infos(provider_ids: list[str] | tuple[str, ...]) -> list[ProviderInfo]:
    return [provider_info(provider_id) for provider_id in provider_ids]
