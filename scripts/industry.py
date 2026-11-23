"""Turn an EDGAR SIC code into a CISA sector and industry news terms.

This is the only bridge between EDGAR and the news source: EDGAR gives the
SIC code, this table gives the terms. The news source never sees the
company; the terms are matched locally against stories already fetched.

Rows are checked top to bottom and the first match wins, so narrow ranges
come before the broad ones that contain them. Terms are matched as whole
words (case-insensitive) in each story's headline and summary, so keep them
specific: "hospital" is good, "health" alone also matches "health check".
"""

from __future__ import annotations

import re

HEALTH = ("hospital", "hospitals", "healthcare", "health system", "medical", "patient", "patients", "clinic", "HIPAA")
PHARMA = ("pharmaceutical", "pharma", "drugmaker", "biotech", "life sciences")
FINANCE = ("bank", "banks", "banking", "financial institution", "financial institutions", "financial services",
           "fintech", "credit union", "payment processor", "brokerage")
INSURANCE = ("insurer", "insurance", "health plan", "health insurer", "medicare", "medicaid")
ENERGY = ("energy", "utility", "utilities", "power grid", "electric", "pipeline", "oil and gas", "nuclear")
OIL_GAS = ("oil and gas", "pipeline", "refinery", "petroleum", "LNG", "drilling")
WATER = ("water utility", "water treatment", "wastewater", "water system")
IT = ("software", "SaaS", "cloud", "IT provider", "managed service provider", "MSP", "supply chain attack")
SEMIS = ("semiconductor", "chipmaker", "chip maker", "foundry")
TELECOM = ("telecom", "telecommunications", "carrier", "ISP", "wireless", "broadband")
MANUFACTURING = ("manufacturing", "manufacturer", "factory", "industrial", "OT", "ICS", "SCADA")
TRANSPORT = ("airline", "airport", "logistics", "shipping", "freight", "railway", "trucking", "port", "transportation")
CRUISE = ("cruise", "cruise line", "cruise lines", "cruise ship", "maritime", "shipping company", "port", "vessel",
          "travel", "hospitality")
AUTO = ("car rental", "rental car", "automotive", "automaker", "car dealership", "dealerships", "vehicle")
RETAIL = ("retail", "retailer", "e-commerce", "point-of-sale", "POS", "store", "shoppers")
HOSPITALITY = ("hotel", "hotels", "hospitality", "casino", "resort")
DEFENSE = ("defense contractor", "defense", "aerospace", "military")
CHEMICAL = ("chemical", "chemicals", "chemical plant")
FOOD = ("food", "agriculture", "farm", "meat", "grocery", "beverage")
EDUCATION = ("university", "college", "school", "school district", "education")
GOVERNMENT = ("government", "agency", "municipal", "city of", "county", "federal", "state agency")
CONSTRUCTION = ("construction", "building automation", "engineering firm")

# (low SIC, high SIC, CISA sector, news terms)
SIC_SECTORS: tuple[tuple[int, int, str, tuple[str, ...]], ...] = (
    (2830, 2836, "Healthcare and Public Health", PHARMA + HEALTH),
    (3570, 3579, "Information Technology", IT),
    (3660, 3669, "Communications", TELECOM),
    (3670, 3679, "Information Technology", SEMIS + IT),
    (3720, 3729, "Defense Industrial Base", DEFENSE),
    (3810, 3812, "Defense Industrial Base", DEFENSE),
    (4941, 4941, "Water and Wastewater Systems", WATER),
    (4952, 4952, "Water and Wastewater Systems", WATER),
    (6320, 6329, "Healthcare and Public Health", INSURANCE + HEALTH),
    (7500, 7549, "Transportation Systems", AUTO),
    (4400, 4499, "Transportation Systems", CRUISE),
    (100, 999, "Food and Agriculture", FOOD),
    (1300, 1399, "Energy", OIL_GAS),
    (1000, 1499, "Critical Manufacturing", ("mining", "mine") + MANUFACTURING),
    (1500, 1799, "Commercial Facilities", CONSTRUCTION),
    (2000, 2099, "Food and Agriculture", FOOD),
    (2800, 2899, "Chemical", CHEMICAL),
    (2900, 2999, "Energy", OIL_GAS),
    (2000, 3999, "Critical Manufacturing", MANUFACTURING),
    (4000, 4799, "Transportation Systems", TRANSPORT),
    (4800, 4899, "Communications", TELECOM),
    (4900, 4999, "Energy", ENERGY),
    (5000, 5999, "Commercial Facilities", RETAIL),
    (6300, 6411, "Financial Services", INSURANCE + FINANCE),
    (6000, 6799, "Financial Services", FINANCE),
    (7000, 7099, "Commercial Facilities", HOSPITALITY),
    (7370, 7379, "Information Technology", IT),
    (8000, 8099, "Healthcare and Public Health", HEALTH),
    (8200, 8299, "Education", EDUCATION),
    (9100, 9999, "Government Services and Facilities", GOVERNMENT),
)

STOPWORDS = {"and", "of", "the", "nec", "misc", "services", "service", "except", "other", "no", "drivers"}


def entry(sector: str | None, terms: tuple[str, ...] | list[str], matched: str | None) -> dict:
    return {"cisa_sector": sector, "news_terms": list(dict.fromkeys(terms)), "matched_sic_range": matched}


def sector_for(sic: int, sic_description: str | None) -> dict:
    for low, high, sector, terms in SIC_SECTORS:
        if low <= sic <= high:
            return entry(sector, terms, f"{low}-{high}")
    return entry(None, fallback_terms(sic_description), None)


def fallback_terms(sic_description: str | None) -> list[str]:
    """'Services-Auto Rental & Leasing (No Drivers)' -> ['auto rental', 'auto', 'rental', 'leasing']."""
    text = re.sub(r"\(.*?\)", " ", sic_description or "")
    text = re.sub(r"^(services|retail|wholesale)-", " ", text.strip(), flags=re.IGNORECASE)
    words = [w for w in re.split(r"[^A-Za-z]+", text.lower()) if len(w) > 2 and w not in STOPWORDS]
    pairs = [" ".join(words[:2])] if len(words) >= 2 else []
    return pairs + words[:4]


def sector_by_name(name: str) -> dict | None:
    """Look up a sector by name, for --sector overrides. Terms from every row of that sector are merged."""
    wanted = name.strip().casefold()
    terms: list[str] = []
    label = None
    for _low, _high, sector, row_terms in SIC_SECTORS:
        if sector.casefold() == wanted:
            label = sector
            terms.extend(row_terms)
    return entry(label, terms, "override") if label else None


def sector_names() -> list[str]:
    return sorted({row[2] for row in SIC_SECTORS})
