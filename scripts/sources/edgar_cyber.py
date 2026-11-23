"""SEC EDGAR: cybersecurity risk signals from the company's own filings.

Uses the same SEC connection as edgar.py (no key, EDGAR_USER_AGENT only).

Signals, strongest first:
    high    8-K with Item 1.05 "Material Cybersecurity Incidents" (required since Dec 2023)
    medium  other 8-Ks whose text mentions an incident, ransomware or unauthorized access
    medium  10-K passages where the company says it has experienced an incident
    info    10-K statements that no material incident occurred, and whether Item 1C exists
"""

from __future__ import annotations

import re
import time

from common import SourceError
from sources import edgar

INCIDENT_QUERY = (
    'cyberattack OR "cyber attack" OR "cybersecurity incident" OR ransomware '
    'OR "unauthorized access" OR "data breach"'
)
ROUTINE_8K_ITEMS = {"2.02", "5.02", "5.07", "9.01"}
CYBER_TERM_RE = re.compile(
    r"\b(cybersecurity incidents?|cyber[- ]?attacks?|ransomware|data breach(?:es)?|"
    r"unauthorized access|security breach(?:es)?|security incidents?|malware)\b",
    re.IGNORECASE,
)
NO_INCIDENT_RE = re.compile(
    r"\b(?:not|no)\b[^.]{0,60}\b(?:experienced|identified|aware of|been materially affected|materially affected)\b",
    re.IGNORECASE,
)
# Past-tense admissions only. "have detected or prevented" style hedges are left out.
EVENT_RE = re.compile(
    r"\b(?:we|the company|our)\b[^.]{0,80}\b(?:(?:have|has|had) experienced|experienced (?:a|an)\b|"
    r"became aware|previously (?:reported|disclosed)|(?:were|was) (?:the )?(?:subject|target|victim) of)",
    re.IGNORECASE,
)
DETECTED_RE = re.compile(
    r"\b(?:identified|detected|discovered)\b[^.]{0,60}\b(?:cyber|ransomware|unauthorized|threat actor)",
    re.IGNORECASE,
)
HEDGE_RE = re.compile(r"forward-looking|could differ materially|no guarantee|force majeure", re.IGNORECASE)
ITEM_1C_RE =re.compile(r"Item\s*1C\.?\s*[-–—:.]?\s*Cybersecurity", re.IGNORECASE)


def archive_url(cik: str, accession: str, document: str) -> str:
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession.replace('-', '')}/{document}"
    )


def item_105_filings(company: dict, refresh: bool) -> list[dict]:
    """8-Ks tagged Item 1.05, read from the structured submissions index."""
    recent = ((edgar.submissions(company, refresh).get("filings") or {}).get("recent")) or {}
    forms = recent.get("form") or []
    found = []
    for index, form in enumerate(forms):
        if form not in ("8-K", "8-K/A"):
            continue
        items = str((recent.get("items") or [""])[index] or "")
        if "1.05" not in items.split(","):
            continue
        accession = recent["accessionNumber"][index]
        found.append(
            {
                "form": form,
                "filed": recent["filingDate"][index],
                "items": items,
                "accession": accession,
                "url": archive_url(company["cik"], accession, recent["primaryDocument"][index]),
            }
        )
    return found


def describes_incident(snippet: str) -> bool:
    if HEDGE_RE.search(snippet):
        return False
    return bool(EVENT_RE.search(snippet) or DETECTED_RE.search(snippet))


def eight_k_mentions(
    company: dict, years: int, refresh: bool, skip: set[str]
) -> tuple[str, list[dict], int]:
    """Other 8-Ks (up to 10 newest) whose text describes an incident.

    Returns the search URL, the matching filings, and how many were dropped
    as boilerplate.
    """
    endpoint, payload = edgar.full_text_search(INCIDENT_QUERY, "8-K", company, years, refresh)
    hits = sorted(
        (payload.get("hits") or {}).get("hits") or [],
        key=lambda hit: str((hit.get("_source") or {}).get("file_date") or ""),
        reverse=True,
    )
    found = []
    seen = set(skip)
    checked = boilerplate = 0
    for hit in hits:
        source = hit.get("_source") or {}
        accession = str(source.get("adsh") or "")
        items = set(source.get("items") or [])
        # Incident news often sits in the press-release exhibit, so exhibits count.
        # Earnings, director and shareholder-vote 8-Ks only repeat old news.
        if not accession or accession in seen or (items and items <= ROUTINE_8K_ITEMS):
            continue
        seen.add(accession)
        checked += 1
        if checked > 10:
            break
        url = edgar.filing_url(hit)
        if not url:
            continue
        time.sleep(0.15)
        # Loan agreements and forward-looking statements mention "cyber-attacks" as a
        # generic risk. Keep the 8-K only if a passage describes an actual incident.
        matches = edgar.passages(edgar.html_to_text(edgar.get_text(url, refresh)), CYBER_TERM_RE, 30)
        snippet = next((m["snippet"] for m in matches if describes_incident(m["snippet"])), None)
        if snippet is None:
            boilerplate += 1
            continue
        found.append(
            {
                "form": source.get("form") or source.get("file_type"),
                "filed": source.get("file_date"),
                "items": ",".join(source.get("items") or []),
                "accession": accession,
                "url": url,
                "snippet": snippet,
            }
        )
    return endpoint, found, boilerplate


def latest_10k_passages(company: dict, refresh: bool) -> tuple[str, dict | None]:
    endpoint, payload = edgar.full_text_search(INCIDENT_QUERY, "10-K", company, 1, refresh)
    hits = edgar.primary_filings((payload.get("hits") or {}).get("hits") or [], 1)
    if not hits:
        return endpoint, None
    hit = hits[0]
    source = hit.get("_source") or {}
    url = edgar.filing_url(hit)
    text = edgar.html_to_text(edgar.get_text(url, refresh)) if url else ""
    found = edgar.passages(text, CYBER_TERM_RE, 200)
    for passage in found:
        if NO_INCIDENT_RE.search(passage["snippet"]):
            passage["kind"] = "no_material_incident"
        elif EVENT_RE.search(passage["snippet"]):
            passage["kind"] = "incident_described"
        else:
            passage["kind"] = "general_risk"
    return endpoint, {
        "form": source.get("form") or source.get("file_type"),
        "filed": source.get("file_date"),
        "accession": source.get("adsh"),
        "url": url,
        "item_1c_present": bool(ITEM_1C_RE.search(text)),
        "passages": found,
    }


def risk_signals(company: dict, years: int, refresh: bool) -> dict:
    if years < 1:
        raise SourceError("--years must be at least 1.")
    signals: list[dict] = []

    material = item_105_filings(company, refresh)
    for filing in material:
        signals.append(
            {
                "level": "high",
                "title": "Material cybersecurity incident disclosed (8-K Item 1.05)",
                "detail": None,
                **{key: filing[key] for key in ("form", "filed", "url")},
            }
        )

    errors: list[str] = []
    try:
        search_8k, other_8k, boilerplate_8k = eight_k_mentions(
            company, years, refresh, {f["accession"] for f in material}
        )
    except SourceError as exc:
        search_8k, other_8k, boilerplate_8k = None, [], 0
        errors.append(f"8-K search: {exc}")
    for filing in other_8k:
        signals.append(
            {
                "level": "medium",
                "title": f"8-K mentions a cyber incident (items {filing['items'] or 'n/a'})",
                "detail": filing["snippet"],
                **{key: filing[key] for key in ("form", "filed", "url")},
            }
        )

    try:
        search_10k, annual = latest_10k_passages(company, refresh)
    except SourceError as exc:
        search_10k, annual = None, None
        errors.append(f"10-K search: {exc}")
    if annual:
        described = [p for p in annual["passages"] if p["kind"] == "incident_described"]
        denied = [p for p in annual["passages"] if p["kind"] == "no_material_incident"]
        for passage in described[:4]:
            signals.append(
                {
                    "level": "medium",
                    "title": "10-K says the company has experienced cyber incidents",
                    "detail": passage["snippet"],
                    **{key: annual[key] for key in ("form", "filed", "url")},
                }
            )
        for passage in denied[:2]:
            signals.append(
                {
                    "level": "info",
                    "title": "10-K states no material cybersecurity incident",
                    "detail": passage["snippet"],
                    **{key: annual[key] for key in ("form", "filed", "url")},
                }
            )
        annual = {
            **{key: annual[key] for key in ("form", "filed", "accession", "url", "item_1c_present")},
            "cyber_passages_found": len(annual["passages"]),
        }

    order = {"high": 0, "medium": 1, "info": 2}
    signals.sort(key=lambda s: str(s.get("filed") or ""), reverse=True)
    signals.sort(key=lambda s: order[s["level"]])  # stable: newest first within each level
    return {
        "topic": "cyber_risk",
        "company": company,
        "summary": {
            "material_incidents_8k": len(material),
            "other_incident_8ks": len(other_8k),
            "boilerplate_8ks_skipped": boilerplate_8k,
            "latest_10k": annual,
        },
        "signals": signals,
        "source": {"search_8k": search_8k, "search_10k": search_10k},
        "errors": errors,
        "note": None if signals else "No cybersecurity signals found in recent 8-K or 10-K filings.",
    }
