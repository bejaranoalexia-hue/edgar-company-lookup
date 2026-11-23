"""SEC EDGAR: who the company is, how big it is, and what industry it is in.

No API key is needed. The SEC asks every caller to identify itself, so set
EDGAR_USER_AGENT to "Your Name you@example.com".

Public functions:
    load_companies / resolve_company   ticker or name -> CIK
    overview                           identity, industry (SIC) and size
    financials                         latest annual statement lines
    search_vendors                     10-K passages naming suppliers or vendors
"""

from __future__ import annotations

import html
import os
import re
import time
import urllib.parse
from datetime import date, timedelta

from common import SourceError, cached_json, cached_text

USER_AGENT = os.environ.get("EDGAR_USER_AGENT") or "EdgarCompanyLookup edgar-lookup@example.com"
HEADERS = {"User-Agent": USER_AGENT}
LABEL = "SEC EDGAR"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A"}
VENDOR_QUERY = 'supplier OR suppliers OR vendor OR vendors OR "supply agreement"'

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
SCRIPT_RE = re.compile(r"(?is)<(script|style|ix:header)\b[^>]*>.*?</\1>")
BLOCK_RE = re.compile(r"(?i)</?(?:p|div|br|tr|h\d|li|table|section)\b[^>]*>")
VENDOR_TERM_RE = re.compile(r"\b(suppliers?|vendors?|supply agreements?)\b", re.IGNORECASE)
PARTY_RE = re.compile(r"\b(?:Inc|Corp|Corporation|Ltd|LLC|L\.P\.|Co)\b\.?")

# Revenue bands used for the one-word size label in the profile.
SIZE_BANDS = (
    (50_000_000, "Small (under $50M revenue)"),
    (1_000_000_000, "Mid-size ($50M to $1B revenue)"),
    (10_000_000_000, "Large ($1B to $10B revenue)"),
)

# Each line tries tags in order. duration=True keeps full-year flow facts.
STATEMENTS = (
    (
        "income_statement",
        (
            ("revenue", "Revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"), True),
            ("cost_of_revenue", "Cost of revenue", ("CostOfRevenue", "CostOfGoodsAndServicesSold"), True),
            ("gross_profit", "Gross profit", ("GrossProfit",), True),
            ("operating_income", "Operating income", ("OperatingIncomeLoss",), True),
            ("interest_expense", "Interest expense", ("InterestExpense", "InterestExpenseDebt"), True),
            ("income_tax", "Income tax", ("IncomeTaxExpenseBenefit",), True),
            ("net_income", "Net income", ("NetIncomeLoss",), True),
            ("eps_diluted", "Diluted EPS", ("EarningsPerShareDiluted",), True),
        ),
    ),
    (
        "balance_sheet",
        (
            ("assets", "Assets", ("Assets",), False),
            ("current_assets", "Current assets", ("AssetsCurrent",), False),
            ("cash", "Cash and cash equivalents", ("CashAndCashEquivalentsAtCarryingValue",), False),
            ("liabilities", "Liabilities", ("Liabilities",), False),
            ("current_liabilities", "Current liabilities", ("LiabilitiesCurrent",), False),
            ("long_term_debt", "Long-term debt", ("LongTermDebt", "LongTermDebtNoncurrent"), False),
            ("stockholders_equity", "Stockholders' equity", ("StockholdersEquity",), False),
        ),
    ),
    (
        "cash_flow",
        (
            ("operating_cash_flow", "Operating cash flow", ("NetCashProvidedByUsedInOperatingActivities",), True),
            ("investing_cash_flow", "Investing cash flow", ("NetCashProvidedByUsedInInvestingActivities",), True),
            ("financing_cash_flow", "Financing cash flow", ("NetCashProvidedByUsedInFinancingActivities",), True),
        ),
    ),
)


def get_json(url: str, refresh: bool) -> dict:
    return cached_json(url, refresh, HEADERS, LABEL)


def get_text(url: str, refresh: bool) -> str:
    return cached_text(url, refresh, HEADERS, LABEL)


# ---------------------------------------------------------------- company lookup


def pad_cik(cik: int | str) -> str:
    return f"{int(cik):010d}"


def load_companies(refresh: bool) -> list[dict]:
    raw = get_json(TICKERS_URL, refresh)
    return [
        {
            "cik": pad_cik(row["cik_str"]),
            "ticker": str(row.get("ticker") or "").upper(),
            "name": str(row.get("title") or "").strip(),
        }
        for row in raw.values()
    ]


def resolve_company(query: str, companies: list[dict]) -> dict:
    text = query.strip()
    if not text:
        raise SourceError("Company ticker or name is required.")
    folded = text.casefold()
    upper = text.upper()

    ticker_hits = [company for company in companies if company["ticker"] == upper]
    if len(ticker_hits) == 1:
        return ticker_hits[0]

    exact = [company for company in companies if company["name"].casefold() == folded]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ambiguous(text, exact)

    partial = [company for company in companies if folded in company["name"].casefold()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        starts = [company for company in partial if company["name"].casefold().startswith(folded)]
        if len(starts) == 1:
            return starts[0]
        raise ambiguous(text, starts or partial)
    raise SourceError(f"No EDGAR company found for '{text}'.")


def ambiguous(query: str, companies: list[dict]) -> SourceError:
    sample = ", ".join(f"{company['ticker']} ({company['name']})" for company in companies[:8])
    return SourceError(f"'{query}' matches more than one company. Use a ticker: {sample}.")


# ---------------------------------------------------------------- overview


def submissions(company: dict, refresh: bool) -> dict:
    return get_json(SUBMISSIONS_URL.format(cik=company["cik"]), refresh)


def latest_dei(facts: dict, tag: str) -> dict | None:
    """Latest value of a cover-page (dei) fact such as public float."""
    concept = ((facts.get("facts") or {}).get("dei") or {}).get(tag)
    if not concept:
        return None
    entries = [entry for units in (concept.get("units") or {}).values() for entry in units]
    entries = [entry for entry in entries if "val" in entry and entry.get("end")]
    if not entries:
        return None
    best = max(entries, key=lambda entry: (entry["end"], str(entry.get("filed") or "")))
    return {"value": best["val"], "as_of": best["end"], "form": best.get("form")}


def size_band(revenue: object) -> str | None:
    if not isinstance(revenue, (int, float)):
        return None
    for limit, label in SIZE_BANDS:
        if revenue < limit:
            return label
    return "Very large (over $10B revenue)"


def overview(company: dict, refresh: bool) -> dict:
    """Identity, industry and size for one company."""
    sub = submissions(company, refresh)
    business = (sub.get("addresses") or {}).get("business") or {}
    try:
        sic = int(sub.get("sic") or 0)
    except ValueError:
        sic = 0

    facts_url = FACTS_URL.format(cik=company["cik"])
    try:
        facts = get_json(facts_url, refresh)
    except SourceError:
        facts = {}
    latest = latest_statement(facts) if facts else None
    revenue = line_value(latest, "income_statement", "revenue")
    assets = line_value(latest, "balance_sheet", "assets")

    return {
        "company": company,
        "identity": {
            "name": sub.get("name") or company["name"],
            "tickers": sub.get("tickers") or [company["ticker"]],
            "exchanges": sub.get("exchanges") or [],
            "entity_type": sub.get("entityType"),
            "state_of_incorporation": sub.get("stateOfIncorporationDescription") or sub.get("stateOfIncorporation"),
            "fiscal_year_end": sub.get("fiscalYearEnd"),
            "website": sub.get("website") or None,
            "phone": sub.get("phone"),
            "business_address": ", ".join(
                part
                for part in (
                    business.get("street1"),
                    business.get("street2"),
                    business.get("city"),
                    business.get("stateOrCountryDescription") or business.get("stateOrCountry"),
                    business.get("zipCode"),
                )
                if part
            ),
            "former_names": [item.get("name") for item in sub.get("formerNames") or [] if item.get("name")],
        },
        "industry": {
            "sic": sic,
            "sic_description": sub.get("sicDescription"),
        },
        "size": {
            "filer_category": sub.get("category"),
            "revenue": revenue,
            "total_assets": assets,
            "size_band": size_band(revenue),
            "fiscal_period_end": latest.get("period_end") if latest else None,
            "public_float_usd": latest_dei(facts, "EntityPublicFloat"),
            "shares_outstanding": latest_dei(facts, "EntityCommonStockSharesOutstanding"),
            "employees": latest_dei(facts, "EntityNumberOfEmployees"),
        },
        "source": {
            "submissions": SUBMISSIONS_URL.format(cik=company["cik"]),
            "facts": facts_url,
        },
    }


# ---------------------------------------------------------------- financials


def annual_facts(entries: list[dict], duration: bool) -> list[dict]:
    chosen: dict[str, dict] = {}
    for item in entries:
        if item.get("form") not in ANNUAL_FORMS or item.get("fp") != "FY":
            continue
        end = item.get("end")
        if not end or "val" not in item:
            continue
        start = item.get("start")
        if duration:
            if not start:
                continue
            span = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if span < 300 or span > 400:
                continue
        elif start:
            span = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if span > 7:
                continue
        previous = chosen.get(end)
        if previous is None or str(item.get("filed") or "") >= str(previous.get("filed") or ""):
            chosen[end] = item
    return sorted(chosen.values(), key=lambda item: item["end"], reverse=True)


def fact_for_end(facts: list[dict], period_end: str) -> dict | None:
    exact = [fact for fact in facts if fact.get("end") == period_end]
    if exact:
        return exact[0]
    target = date.fromisoformat(period_end)
    closest: tuple[int, dict] | None = None
    for fact in facts:
        delta = abs((date.fromisoformat(fact["end"]) - target).days)
        if delta <= 7 and (closest is None or delta < closest[0]):
            closest = (delta, fact)
    return closest[1] if closest else None


def line_payload(key: str, label: str, taxonomy: str, unit: str, tag: str, fact: dict | None) -> dict:
    if fact is None:
        return {"key": key, "label": label, "value": None, "missing": True}
    return {
        "key": key,
        "label": label,
        "taxonomy": taxonomy,
        "tag": tag,
        "value": fact.get("val"),
        "unit": unit,
        "start": fact.get("start"),
        "end": fact.get("end"),
        "form": fact.get("form"),
        "fiscal_year": fact.get("fy"),
        "filed": fact.get("filed"),
        "accession": fact.get("accn"),
    }


def resolve_line(facts: dict, key: str, label: str, tags: tuple[str, ...], duration: bool) -> dict:
    for taxonomy in ("us-gaap", "ifrs-full"):
        concepts = facts.get(taxonomy) or {}
        for tag in tags:
            concept = concepts.get(tag)
            if not concept:
                continue
            units = concept.get("units") or {}
            if "USD" in units:
                unit_name, entries = "USD", units["USD"]
            elif "USD/shares" in units:
                unit_name, entries = "USD/shares", units["USD/shares"]
            elif units:
                unit_name, entries = next(iter(units.items()))
            else:
                continue
            annual = annual_facts(entries, duration)
            if annual:
                return {
                    "key": key,
                    "label": concept.get("label") or label,
                    "taxonomy": taxonomy,
                    "tag": tag,
                    "unit": unit_name,
                    "facts": annual,
                }
    return {"key": key, "label": label, "taxonomy": "", "tag": "", "unit": "", "facts": []}


def statement_periods(payload: dict, years: int) -> list[dict]:
    fact_root = payload.get("facts") or {}
    resolved = []
    for statement_name, lines in STATEMENTS:
        for key, label, tags, duration in lines:
            item = resolve_line(fact_root, key, label, tags, duration)
            item["statement"] = statement_name
            resolved.append(item)

    anchors = next((item["facts"] for item in resolved if item["key"] == "revenue" and item["facts"]), [])
    if not anchors:
        anchors = next((item["facts"] for item in resolved if item["facts"]), [])
    period_ends: list[str] = []
    for fact in anchors:
        if fact["end"] not in period_ends:
            period_ends.append(fact["end"])
        if len(period_ends) == years:
            break

    periods = []
    for period_end in period_ends:
        statements: dict[str, list[dict]] = {name: [] for name, _lines in STATEMENTS}
        for item in resolved:
            fact = fact_for_end(item["facts"], period_end)
            statements[item["statement"]].append(
                line_payload(item["key"], item["label"], item["taxonomy"], item["unit"], item["tag"], fact)
            )
        sample = next(
            (line for lines in statements.values() for line in lines if line.get("accession")),
            {},
        )
        periods.append(
            {
                "period_end": period_end,
                "fiscal_year": sample.get("fiscal_year"),
                "form": sample.get("form"),
                "filed": sample.get("filed"),
                "accession": sample.get("accession"),
                **statements,
            }
        )
    return periods


def latest_statement(payload: dict) -> dict | None:
    periods = statement_periods(payload, 1)
    return periods[0] if periods else None


def line_value(period: dict | None, statement: str, key: str) -> object:
    if not period:
        return None
    for line in period.get(statement) or []:
        if line.get("key") == key:
            return line.get("value")
    return None


def financials(company: dict, years: int, refresh: bool) -> dict:
    if years < 1:
        raise SourceError("--years must be at least 1.")
    url = FACTS_URL.format(cik=company["cik"])
    periods = statement_periods(get_json(url, refresh), years)
    if not periods:
        raise SourceError(
            f"No annual 10-K or 20-F financial facts found for {company['name']} ({company['ticker']})."
        )
    return {
        "topic": "financials",
        "company": company,
        "source": {"endpoint": url},
        "statements": periods,
    }


# ---------------------------------------------------------------- full-text search


def full_text_search(query: str, forms: str, company: dict, years: int, refresh: bool) -> tuple[str, dict]:
    end = date.today()
    start = end - timedelta(days=365 * (years + 1))
    params = urllib.parse.urlencode(
        {
            "q": query,
            "forms": forms,
            "ciks": company["cik"],
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
        }
    )
    endpoint = f"{SEARCH_URL}?{params}"
    return endpoint, get_json(endpoint, refresh)


def filing_url(hit: dict) -> str | None:
    source = hit.get("_source") or {}
    hit_id = str(hit.get("_id") or "")
    filename = ""
    accession = str(source.get("adsh") or "")
    if ":" in hit_id:
        accession_from_id, filename = hit_id.split(":", 1)
        accession = accession or accession_from_id
    ciks = source.get("ciks") or []
    if not accession or not filename or not ciks:
        return None
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(ciks[0])}/{accession.replace('-', '')}/{filename}"
    )


def primary_filings(hits: list[dict], limit: int) -> list[dict]:
    """Newest main documents (not exhibits), one per accession."""
    ranked = sorted(
        hits,
        key=lambda hit: str((hit.get("_source") or {}).get("file_date") or ""),
        reverse=True,
    )
    chosen = []
    seen: set[str] = set()
    for hit in ranked:
        source = hit.get("_source") or {}
        filename = str(hit.get("_id") or "").split(":", 1)[-1].lower()
        accession = str(source.get("adsh") or "")
        if not accession or accession in seen or "exhibit" in filename:
            continue
        if source.get("sequence") not in (1, "1"):
            continue
        seen.add(accession)
        chosen.append(hit)
        if len(chosen) == limit:
            break
    return chosen


def html_to_text(document: str) -> str:
    document = SCRIPT_RE.sub(" ", document)
    document = BLOCK_RE.sub("\n", document)
    document = TAG_RE.sub(" ", document)
    return html.unescape(document)


def passages(text: str, term_re: re.Pattern[str], limit: int) -> list[dict]:
    """Paragraphs (or sentences of long paragraphs) that match term_re."""
    found = []
    seen: set[str] = set()
    for chunk in text.split("\n"):
        line = SPACE_RE.sub(" ", chunk).strip()
        pieces = [line] if len(line) <= 900 else re.split(r"(?<=\.)\s+", line)
        for piece in pieces:
            piece = piece.strip()
            match = term_re.search(piece)
            if not match or len(piece) < 40:
                continue
            snippet = piece if len(piece) <= 700 else piece[:700].rstrip() + "..."
            key = snippet.casefold()[:180]
            if key in seen:
                continue
            seen.add(key)
            found.append({"term": match.group(0).lower(), "snippet": snippet})
            if len(found) >= limit:
                return found
    return found


def search_vendors(company: dict, years: int, refresh: bool) -> dict:
    if years < 1:
        raise SourceError("--years must be at least 1.")
    endpoint, payload = full_text_search(VENDOR_QUERY, "10-K", company, years, refresh)
    hits_root = payload.get("hits") or {}
    total = hits_root.get("total")
    total_value = total.get("value") if isinstance(total, dict) else total
    mentions = []
    for index, hit in enumerate(primary_filings(hits_root.get("hits") or [], years)):
        if index:
            time.sleep(0.15)
        source = hit.get("_source") or {}
        url = filing_url(hit)
        found = passages(html_to_text(get_text(url, refresh)), VENDOR_TERM_RE, 40) if url else []
        for passage in found:
            passage["names_a_party"] = bool(PARTY_RE.search(passage["snippet"]))
        found.sort(key=lambda item: not item["names_a_party"])
        for passage in found[:8]:
            mentions.append(
                {
                    **passage,
                    "form": source.get("form") or source.get("file_type"),
                    "filed": source.get("file_date"),
                    "period_end": source.get("period_ending"),
                    "accession": source.get("adsh"),
                    "filing_url": url,
                }
            )
    return {
        "topic": "vendors",
        "company": company,
        "source": {"endpoint": endpoint, "hits": total_value or 0},
        "mentions": mentions,
        "note": None
        if mentions
        else "No supplier or vendor passages found in this company's 10-K filings for the search window.",
    }
