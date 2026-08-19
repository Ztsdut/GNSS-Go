from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.bkg import BKGProvider
from gnssgo.providers.igs_aux import IGSAuxiliaryProvider
from gnssgo.providers.mirrors import (
    bdsmart_provider,
    bkgftp_provider,
    esa_provider,
    ign_provider,
    kasi_provider,
    noaa_provider,
    sopac_provider,
)
from gnssgo.providers.americas import (
    BoliviaSIRGASProvider,
    ChileCSNProvider,
    ColombiaSIRGASProvider,
    CostaRicaSIRGASProvider,
    EcuadorSIRGASProvider,
    PanamaSIRGASProvider,
    ParaguaySIRGASProvider,
    VenezuelaSIRGASProvider,
    GuyanaSIRGASProvider,
    SurinameSIRGASProvider,
    PeruSIRGASProvider,
    RAMSACArgentinaProvider,
    RGNAMexicoProvider,
    SIRGASRBMCProvider,
    UruguaySIRGASProvider,
)
from gnssgo.providers.regional import regional_placeholder_providers
from gnssgo.providers.korea import KoreaKASIFTPProvider, KoreaNationalCatalogProvider
from gnssgo.providers.japan import JapanGEONETProvider
from gnssgo.providers.china_region import TaiwanGDMSProvider, CMONOCChinaProvider
from gnssgo.providers.regional_expansion import (
    CACSCanadaProvider,
    CHAINCanadaProvider,
    DPGANetherlandsProvider,
    GREFGermanyProvider,
    NOAANCNProvider,
    NOAGreeceProvider,
    ItalyEPOSProvider,
    PolandEPOSProvider,
    RomaniaEPOSProvider,
    UnitedKingdomEPOSProvider,
    SwedenEPOSProvider,
    FinlandEPOSProvider,
    SwitzerlandEPOSProvider,
    HungaryEPOSProvider,
    CzechiaEPOSProvider,
    SloveniaEPOSProvider,
    IrelandEPOSProvider,
    IcelandEPOSProvider,
    CroatiaEPOSProvider,
    NorwayEPOSProvider,
    DenmarkEPOSProvider,
    EstoniaEPOSProvider,
    LatviaEPOSProvider,
    LithuaniaEPOSProvider,
    SlovakiaEPOSProvider,
    BulgariaEPOSProvider,
    CyprusEPOSProvider,
    SerbiaEPOSProvider,
    TurkeyEPOSProvider,
    LuxembourgEPOSProvider,
    AlbaniaEPOSProvider,
    BosniaEPOSProvider,
    NorthMacedoniaEPOSProvider,
    MoldovaEPOSProvider,
    UkraineEPOSProvider,
    MaltaEPOSProvider,
    MontenegroEPOSProvider,
    NSGINetherlandsProvider,
    APOSAustriaProvider,
    BelgiumGNSSProvider,
    ReNEPPortugalProvider,
    RENAGFranceProvider,
    RGPFranceProvider,
    RedGAESpainProvider,
    SatRefHKProvider,
)
from gnssgo.providers.regional_live import (
    EPNProvider,
    GAProvider,
    GeoNetNZProvider,
    RBMCProvider,
)
from gnssgo.providers.registry import ProviderRegistry
from gnssgo.providers.whu import WHUProvider


def default_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(WHUProvider())
    registry.register(bdsmart_provider())
    registry.register(kasi_provider())
    registry.register(bkgftp_provider())
    registry.register(esa_provider())
    registry.register(ign_provider())
    registry.register(sopac_provider())
    registry.register(noaa_provider())
    registry.register(IGSAuxiliaryProvider())
    registry.register(BKGProvider())
    registry.register(GAProvider())
    registry.register(EPNProvider())
    registry.register(GeoNetNZProvider())
    registry.register(RBMCProvider())
    registry.register(RAMSACArgentinaProvider())
    registry.register(SIRGASRBMCProvider())
    registry.register(ChileCSNProvider())
    registry.register(RGNAMexicoProvider())
    registry.register(BoliviaSIRGASProvider())
    registry.register(ColombiaSIRGASProvider())
    registry.register(EcuadorSIRGASProvider())
    registry.register(PeruSIRGASProvider())
    registry.register(UruguaySIRGASProvider())
    registry.register(CostaRicaSIRGASProvider())
    registry.register(PanamaSIRGASProvider())
    registry.register(ParaguaySIRGASProvider())
    registry.register(VenezuelaSIRGASProvider())
    registry.register(GuyanaSIRGASProvider())
    registry.register(SurinameSIRGASProvider())
    registry.register(DPGANetherlandsProvider())
    registry.register(NSGINetherlandsProvider())
    registry.register(APOSAustriaProvider())
    registry.register(BelgiumGNSSProvider())
    registry.register(NOAGreeceProvider())
    registry.register(ItalyEPOSProvider())
    registry.register(PolandEPOSProvider())
    registry.register(RomaniaEPOSProvider())
    registry.register(UnitedKingdomEPOSProvider())
    registry.register(SwedenEPOSProvider())
    registry.register(FinlandEPOSProvider())
    registry.register(SwitzerlandEPOSProvider())
    registry.register(HungaryEPOSProvider())
    registry.register(CzechiaEPOSProvider())
    registry.register(SloveniaEPOSProvider())
    registry.register(IrelandEPOSProvider())
    registry.register(IcelandEPOSProvider())
    registry.register(CroatiaEPOSProvider())
    registry.register(NorwayEPOSProvider())
    registry.register(DenmarkEPOSProvider())
    registry.register(EstoniaEPOSProvider())
    registry.register(LatviaEPOSProvider())
    registry.register(LithuaniaEPOSProvider())
    registry.register(SlovakiaEPOSProvider())
    registry.register(BulgariaEPOSProvider())
    registry.register(CyprusEPOSProvider())
    registry.register(SerbiaEPOSProvider())
    registry.register(TurkeyEPOSProvider())
    registry.register(LuxembourgEPOSProvider())
    registry.register(AlbaniaEPOSProvider())
    registry.register(BosniaEPOSProvider())
    registry.register(NorthMacedoniaEPOSProvider())
    registry.register(MoldovaEPOSProvider())
    registry.register(UkraineEPOSProvider())
    registry.register(MaltaEPOSProvider())
    registry.register(MontenegroEPOSProvider())
    registry.register(ReNEPPortugalProvider())
    registry.register(RENAGFranceProvider())
    registry.register(RGPFranceProvider())
    registry.register(GREFGermanyProvider())
    registry.register(RedGAESpainProvider())
    registry.register(CACSCanadaProvider())
    registry.register(CHAINCanadaProvider())
    registry.register(SatRefHKProvider())
    registry.register(NOAANCNProvider())
    registry.register(JapanGEONETProvider())
    registry.register(TaiwanGDMSProvider())
    registry.register(CMONOCChinaProvider())
    registry.register(KoreaKASIFTPProvider())
    registry.register(KoreaNationalCatalogProvider())
    for provider in regional_placeholder_providers():
        if provider.name not in registry.names():
            registry.register(provider)
    return registry


__all__ = [
    "BKGProvider",
    "CACSCanadaProvider",
    "CHAINCanadaProvider",
    "DPGANetherlandsProvider",
    "NSGINetherlandsProvider",
    "APOSAustriaProvider",
    "BelgiumGNSSProvider",
    "ReNEPPortugalProvider",
    "GREFGermanyProvider",
    "EPNProvider",
    "GAProvider",
    "GNSSProvider",
    "GeoNetNZProvider",
    "IGSAuxiliaryProvider",
    "NOAANCNProvider",
    "KoreaKASIFTPProvider",
    "KoreaNationalCatalogProvider",
    "JapanGEONETProvider",
    "TaiwanGDMSProvider",
    "CMONOCChinaProvider",
    "NOAGreeceProvider",
    "ItalyEPOSProvider",
    "PolandEPOSProvider",
    "RomaniaEPOSProvider",
    "UnitedKingdomEPOSProvider",
    "SwedenEPOSProvider",
    "FinlandEPOSProvider",
    "SwitzerlandEPOSProvider",
    "ProviderCapabilities",
    "ProviderRegistry",
    "RBMCProvider",
    "RENAGFranceProvider",
    "RGPFranceProvider",
    "RedGAESpainProvider",
    "SatRefHKProvider",
    "WHUProvider",
    "bdsmart_provider",
    "bkgftp_provider",
    "default_registry",
    "esa_provider",
    "ign_provider",
    "kasi_provider",
    "noaa_provider",
    "sopac_provider",
]
