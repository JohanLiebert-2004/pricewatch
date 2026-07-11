from .bigw import BigWScraper
from .chemistwarehouse import ChemistWarehouseScraper
from .goodguys import GoodGuysScraper
from .jbhifi import JBHiFiScraper
from .kmart_group import KmartScraper, TargetScraper
from .myer import MyerScraper
from .officeworks import OfficeworksScraper
from .sephora import SephoraScraper
from .supercheap import SupercheapScraper

REGISTRY = {s.name: s for s in (BigWScraper, KmartScraper, TargetScraper,
                                OfficeworksScraper, JBHiFiScraper, GoodGuysScraper,
                                SupercheapScraper, SephoraScraper,
                                ChemistWarehouseScraper, MyerScraper)}
