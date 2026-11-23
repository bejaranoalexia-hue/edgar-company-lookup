"""Build one target profile from EDGAR, CybersecurityNews.us and CISA KEV.

    py -3 scripts/build_profile.py AAPL > build/profile.json
    py -3 scripts/build_profile.py "Hertz" --tech "Microsoft,Cisco,Fortinet"
    py -3 scripts/build_profile.py JPM --sector "Financial Services" --no-news

Sections, one per source:
    company       EDGAR: identity, industry, size, latest financials, vendors
    cyber_risk    EDGAR: 8-K Item 1.05 and other incident 8-Ks, 10-K cyber passages
    industry      SIC code mapped to a CISA sector and news queries
    industry_news CybersecurityNews.us search on those queries (NEWS_API_KEY)
    kev           CISA KEV exposure for named or filing-mentioned vendors

A failure in one section is recorded in that section and the rest still run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import industry
from common import SourceError
from sources import edgar, edgar_cyber, kev, news


def run(label: str, errors: list[str], func, *args):
    try:
        return func(*args)
    except SourceError as exc:
        errors.append(f"{label}: {exc}")
        return {"error": str(exc)}


def build(args: argparse.Namespace) -> dict:
    errors: list[str] = []
    company = edgar.resolve_company(args.company, edgar.load_companies(args.refresh))

    overview = run("company", errors, edgar.overview, company, args.refresh)
    financials = run("financials", errors, edgar.financials, company, args.years, args.refresh)
    vendors = run("vendors", errors, edgar.search_vendors, company, args.years, args.refresh)
    cyber = run("cyber_risk", errors, edgar_cyber.risk_signals, company, args.cyber_years, args.refresh)

    sic_info = overview.get("industry") or {}
    sector = industry.sector_by_name(args.sector) if args.sector else None
    if args.sector and sector is None:
        errors.append(f"industry: unknown sector '{args.sector}'. Choose from: {', '.join(industry.sector_names())}.")
    if sector is None:
        sector = industry.sector_for(int(sic_info.get("sic") or 0), sic_info.get("sic_description"))
    industry_section = {**sic_info, **sector}

    if args.no_news:
        news_section = {"topic": "industry_news", "configured": False, "items": [], "note": "Skipped with --no-news."}
    else:
        news_section = run("industry_news", errors, news.search_industry, sector["news_terms"], args.refresh)

    filing_text = "\n".join(
        [f"{v.get('name')} {v.get('quote') or ''}" for v in vendors.get("vendors") or []]
        + [s.get("detail") or "" for s in cyber.get("signals") or []]
    )
    tech = [name for name in (args.tech or "").split(",") if name.strip()]
    kev_section = run("kev", errors, kev.exposure, tech, filing_text, args.refresh)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "company": {**overview, "financials": financials, "vendors": vendors},
        "cyber_risk": cyber,
        "industry": industry_section,
        "industry_news": news_section,
        "kev": kev_section,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a company and cyber-risk profile from public sources.")
    parser.add_argument("company", help="Ticker or company name, for example AAPL or Apple")
    parser.add_argument("--tech", help="Comma-separated vendors the company is known to use, checked against CISA KEV")
    parser.add_argument("--sector", help="Override the CISA sector picked from the SIC code")
    parser.add_argument("--years", type=int, default=1, help="Annual statements and 10-Ks to read. Default 1.")
    parser.add_argument("--cyber-years", type=int, default=3, help="Years of 8-Ks to search for incidents. Default 3.")
    parser.add_argument("--no-news", action="store_true", help="Skip the news API (saves trial quota).")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached responses.")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        profile = build(args)
    except SourceError as exc:
        json.dump({"error": str(exc)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1
    json.dump(profile, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
