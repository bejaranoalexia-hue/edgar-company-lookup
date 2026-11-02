"""Look up one company's SEC EDGAR financial statements or vendor disclosures.

The only two topics are:

    py -3 scripts/edgar_lookup.py financials AAPL
    py -3 scripts/edgar_lookup.py vendors AAPL

financials reads the XBRL company-facts endpoint and returns the latest
annual income statement, balance sheet, and cash flow lines.
vendors searches the EDGAR full-text endpoint, then reads the matching 10-K
and returns passages that mention suppliers or vendors.

Set EDGAR_USER_AGENT to "Your Name you@example.com". The SEC rejects
requests that do not identify the caller.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT",
    "EdgarCompanyLookup edgar-lookup@example.com",
)
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
CACHE_DIR = Path(__file__).resolve().parent / ".edgar_cache"
CACHE_TTL_SECONDS = 12 * 60 * 60
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A"}
VENDOR_QUERY = 'supplier OR suppliers OR vendor OR vendors OR "supply agreement"'
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
SCRIPT_RE = re.compile(r"(?is)<(script|style|ix:header)\b[^>]*>.*?</\1>")
BLOCK_RE = re.compile(r"(?i)</?(?:p|div|br|tr|h\d|li|table|section)\b[^>]*>")
TERM_RE = re.compile(r"\b(suppliers?|vendors?|supply agreements?)\b", re.IGNORECASE)
PARTY_RE = re.compile(r"\b(?:Inc|Corp|Corporation|Ltd|LLC|L\.P\.|Co)\b\.?")

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


class EdgarError(Exception):
    """A lookup failed and the caller should print a JSON error."""


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise EdgarError(f"SEC returned HTTP {exc.code} for {url}. {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise EdgarError(f"Could not reach SEC for {url}. {exc.reason}") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EdgarError(f"SEC returned non-JSON for {url}.") from exc


def cached_json(url: str, refresh: bool) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.json"
    if not refresh and path.exists():
        age = time.time() - path.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            return json.loads(path.read_text(encoding="utf-8"))
    data = fetch_json(url)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def pad_cik(cik: int | str) -> str:
    return f"{int(cik):010d}"


def load_companies(refresh: bool) -> list[dict]:
    raw = cached_json(TICKERS_URL, refresh)
    companies = []
    for row in raw.values():
        companies.append(
            {
                "cik": pad_cik(row["cik_str"]),
                "ticker": str(row.get("ticker") or "").upper(),
                "name": str(row.get("title") or "").strip(),
            }
        )
    return companies


def resolve_company(query: str, companies: list[dict]) -> dict:
    text = query.strip()
    if not text:
        raise EdgarError("Company ticker or name is required.")
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
    raise EdgarError(f"No EDGAR company found for '{text}'.")


def ambiguous(query: str, companies: list[dict]) -> EdgarError:
    sample = ", ".join(f"{company['ticker']} ({company['name']})" for company in companies[:8])
    return EdgarError(f"'{query}' matches more than one company. Use a ticker: {sample}.")


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


def financials(company: dict, years: int, refresh: bool) -> dict:
    if years < 1:
        raise EdgarError("--years must be at least 1.")
    url = FACTS_URL.format(cik=company["cik"])
    payload = cached_json(url, refresh)
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
    period_ends = []
    for fact in anchors:
        if fact["end"] not in period_ends:
            period_ends.append(fact["end"])
        if len(period_ends) == years:
            break
    if not period_ends:
        raise EdgarError(
            f"No annual 10-K or 20-F financial facts found for {company['name']} ({company['ticker']})."
        )

    periods = []
    for period_end in period_ends:
        statements: dict[str, list[dict]] = {name: [] for name, _lines in STATEMENTS}
        for item in resolved:
            fact = fact_for_end(item["facts"], period_end)
            statements[item["statement"]].append(
                line_payload(item["key"], item["label"], item["taxonomy"], item["unit"], item["tag"], fact)
            )
        sample = next(
            (
                line
                for lines in statements.values()
                for line in lines
                if line.get("accession")
            ),
            {},
        )
        periods.append(
            {
                "period_end": period_end,
                "fiscal_year": sample.get("fiscal_year"),
                "form": sample.get("form"),
                "filed": sample.get("filed"),
                "accession": sample.get("accession"),
                "income_statement": statements["income_statement"],
                "balance_sheet": statements["balance_sheet"],
                "cash_flow": statements["cash_flow"],
            }
        )

    return {
        "topic": "financials",
        "company": company,
        "source": {"endpoint": url},
        "statements": periods,
    }


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


def cached_text(url: str, refresh: bool) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.html"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
        return path.read_text(encoding="utf-8")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            document = response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise EdgarError(f"SEC returned HTTP {exc.code} for {url}.") from exc
    except urllib.error.URLError as exc:
        raise EdgarError(f"Could not reach SEC for {url}. {exc.reason}") from exc
    path.write_text(document, encoding="utf-8")
    return document


def primary_filings(hits: list[dict], limit: int) -> list[dict]:
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


def passages_from_html(document: str) -> list[dict]:
    document = SCRIPT_RE.sub(" ", document)
    document = BLOCK_RE.sub("\n", document)
    document = TAG_RE.sub(" ", document)
    document = html.unescape(document)
    found = []
    seen: set[str] = set()
    for chunk in document.split("\n"):
        text = SPACE_RE.sub(" ", chunk).strip()
        pieces = [text] if len(text) <= 900 else re.split(r"(?<=\.)\s+", text)
        for piece in pieces:
            piece = piece.strip()
            match = TERM_RE.search(piece)
            if not match or len(piece) < 40:
                continue
            snippet = piece if len(piece) <= 700 else piece[:700].rstrip() + "..."
            key = snippet.casefold()[:180]
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "term": match.group(0).lower(),
                    "snippet": snippet,
                    "names_a_party": bool(PARTY_RE.search(snippet)),
                }
            )
    found.sort(key=lambda item: (not item["names_a_party"],))
    return found[:8]


def search_vendors(company: dict, years: int, refresh: bool) -> dict:
    if years < 1:
        raise EdgarError("--years must be at least 1.")
    end = date.today()
    start = end - timedelta(days=365 * (years + 1))
    params = urllib.parse.urlencode(
        {
            "q": VENDOR_QUERY,
            "forms": "10-K",
            "ciks": company["cik"],
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
        }
    )
    search_endpoint = f"{SEARCH_URL}?{params}"
    payload = cached_json(search_endpoint, refresh)
    hits_root = payload.get("hits") or {}
    total = hits_root.get("total")
    total_value = total.get("value") if isinstance(total, dict) else total
    filings = primary_filings(hits_root.get("hits") or [], years)
    mentions = []
    for index, hit in enumerate(filings):
        if index:
            time.sleep(0.15)
        source = hit.get("_source") or {}
        url = filing_url(hit)
        passages = passages_from_html(cached_text(url, refresh)) if url else []
        for passage in passages:
            mentions.append(
                {
                    "term": passage["term"],
                    "names_a_party": passage["names_a_party"],
                    "form": source.get("form") or source.get("file_type"),
                    "filed": source.get("file_date"),
                    "period_end": source.get("period_ending"),
                    "accession": source.get("adsh"),
                    "filing_url": url,
                    "snippet": passage["snippet"],
                }
            )
    return {
        "topic": "vendors",
        "company": company,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "source": {"endpoint": search_endpoint, "hits": total_value or 0},
        "mentions": mentions,
        "note": (
            None
            if mentions
            else "No supplier or vendor passages found in this company's 10-K filings for the search window."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull SEC EDGAR financial statements or vendor disclosures for one company."
    )
    parser.add_argument("topic", choices=("financials", "vendors"))
    parser.add_argument("company", help="Ticker or company name, for example AAPL or Apple")
    parser.add_argument(
        "--years",
        type=int,
        default=None,
        help="How many latest annual statements or 10-Ks to return. Default is 1.",
    )
    parser.add_argument("--refresh", action="store_true", help="Ignore cached SEC responses.")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    years = args.years if args.years is not None else 1
    try:
        company = resolve_company(args.company, load_companies(args.refresh))
        if args.topic == "financials":
            result = financials(company, years, args.refresh)
        else:
            result = search_vendors(company, years, args.refresh)
    except EdgarError as exc:
        json.dump({"error": str(exc)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
