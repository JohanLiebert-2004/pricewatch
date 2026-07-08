from .bigw import BigWScraper
from .goodguys import GoodGuysScraper
from .jbhifi import JBHiFiScraper
from .kmart_group import KmartScraper, TargetScraper
from .officeworks import OfficeworksScraper
from .supercheap import SupercheapScraper

REGISTRY = {s.name: s for s in (BigWScraper, KmartScraper, TargetScraper,
                                OfficeworksScraper, JBHiFiScraper, GoodGuysScraper,
                                SupercheapScraper)}
