from .bigw import BigWScraper
from .kmart_group import KmartScraper, TargetScraper
from .officeworks import OfficeworksScraper

REGISTRY = {s.name: s for s in (BigWScraper, KmartScraper, TargetScraper, OfficeworksScraper)}
