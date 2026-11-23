"""CISA Known Exploited Vulnerabilities (KEV) catalog: exposure by vendor.

The catalog is a free public JSON file, no key needed. It is downloaded
once a day and filtered locally, so nothing about the target is sent.

KEV is organized by vendor and product, not by industry, so it needs a
list of technology names to check. Two inputs are combined:
    * names you pass in (for example what you know the company runs)
    * KEV vendor names that appear in the company's own 10-K text
      (supplier passages and cyber passages from the EDGAR modules)
"""

from __future__ import annotations

import re
from collections import defaultdict

from common import cached_json

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
LABEL = "CISA KEV"
HEADERS = {"User-Agent": "EdgarCompanyLookup"}
CACHE_TTL_SECONDS = 24 * 60 * 60
RECENT_PER_VENDOR = 5

# KEV vendor names that are also ordinary English words or too generic to
# match safely inside filing text. They still match when passed explicitly.
TEXT_STOPLIST = {"progress", "code", "multiple", "unknown", "open", "craft", "web", "digital", "general", "access"}


def catalog(refresh: bool) -> dict:
    return cached_json(KEV_URL, refresh, HEADERS, LABEL, ttl=CACHE_TTL_SECONDS)


def by_vendor(data: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for vuln in data.get("vulnerabilities") or []:
        vendor = str(vuln.get("vendorProject") or "").strip()
        if vendor:
            grouped[vendor].append(vuln)
    return grouped


def vendors_in_text(text: str, vendors: list[str]) -> set[str]:
    found = set()
    for vendor in vendors:
        if vendor.casefold() in TEXT_STOPLIST or len(vendor) < 3:
            continue
        # Case-sensitive whole word, so "Oracle" matches but "oracle" in prose does not.
        if re.search(rf"(?<![\w-]){re.escape(vendor)}(?![\w-])", text):
            found.add(vendor)
    return found


def vendors_named(names: list[str], vendors: list[str]) -> set[str]:
    wanted = {name.strip().casefold() for name in names if name.strip()}
    return {vendor for vendor in vendors if vendor.casefold() in wanted}


def exposure(names: list[str], filing_text: str, refresh: bool) -> dict:
    data = catalog(refresh)
    grouped = by_vendor(data)
    vendors = sorted(grouped)

    explicit = vendors_named(names, vendors)
    from_filings = vendors_in_text(filing_text, vendors) - explicit
    unmatched = sorted(
        name.strip()
        for name in names
        if name.strip() and name.strip().casefold() not in {v.casefold() for v in explicit}
    )

    results = []
    for vendor in sorted(explicit | from_filings):
        vulns = sorted(grouped[vendor], key=lambda v: str(v.get("dateAdded") or ""), reverse=True)
        results.append(
            {
                "vendor": vendor,
                "matched_from": "your list" if vendor in explicit else "company filings",
                "kev_count": len(vulns),
                "ransomware_count": sum(1 for v in vulns if v.get("knownRansomwareCampaignUse") == "Known"),
                "latest_added": vulns[0].get("dateAdded") if vulns else None,
                "recent": [
                    {
                        "cve": v.get("cveID"),
                        "product": v.get("product"),
                        "name": v.get("vulnerabilityName"),
                        "date_added": v.get("dateAdded"),
                        "ransomware": v.get("knownRansomwareCampaignUse") == "Known",
                    }
                    for v in vulns[:RECENT_PER_VENDOR]
                ],
            }
        )
    results.sort(key=lambda r: (r["matched_from"] != "your list", -r["ransomware_count"], -r["kev_count"]))
    return {
        "topic": "kev",
        "catalog_version": data.get("catalogVersion"),
        "catalog_released": data.get("dateReleased"),
        "vendors": results,
        "unmatched_names": unmatched,
        "source": {"endpoint": KEV_URL},
        "note": None if results else "No KEV vendors matched the names given or the company's filing text.",
    }
