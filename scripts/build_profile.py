"""Build one target profile from EDGAR, CybersecurityNews.us and CISA KEV.

    py -3 scripts/build_profile.py AAPL > build/profile.json
    py -3 scripts/build_profile.py "Hertz" --tech "Microsoft,Cisco,Fortinet"
    py -3 scripts/build_profile.py JPM --sector "Financial Services" --no-news
    py -3 scripts/build_profile.py "Azamara Cruises" --private --aliases Azamara --sic 4400

Private companies (--private) file nothing with the SEC. For them the
company section lists what other companies' filings say about them
(former owners, vendors, board biographies), and there are no 8-K or
10-K cyber signals.

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


def split_list(text: str | None) -> list[str]:
    return [item.strip() for item in (text or "").split(",") if item.strip()]


def public_sections(args: argparse.Namespace, errors: list[str]) -> tuple[dict, dict, dict]:
    """EDGAR sections for a company that files with the SEC."""
    company = edgar.resolve_company(args.company, edgar.load_companies(args.refresh))
    overview = run("company", errors, edgar.overview, company, args.refresh)
    financials = run("financials", errors, edgar.financials, company, args.years, args.refresh)
    vendors = run("vendors", errors, edgar.search_vendors, company, args.years, args.refresh)
    cyber = run("cyber_risk", errors, edgar_cyber.risk_signals, company, args.cyber_years, args.refresh)
    filing_text = "\n".join(
        [f"{v.get('name')} {v.get('quote') or ''}" for v in vendors.get("vendors") or []]
        + [s.get("detail") or "" for s in cyber.get("signals") or []]
    )
    return {**overview, "financials": financials, "vendors": vendors}, cyber, {"text": filing_text}


def private_sections(args: argparse.Namespace, errors: list[str]) -> tuple[dict, dict, dict]:
    """A private company files nothing, so EDGAR only shows what other companies say about it."""
    names = [args.company] + split_list(args.aliases)
    mentions = run("mentions_elsewhere", errors, edgar.mentions_elsewhere, names, args.mention_years, args.refresh)
    company = {
        "company": {"name": args.company, "ticker": "", "cik": None},
        "private": True,
        "identity": {"name": args.company, "aliases": names[1:]},
        "industry": {"sic": args.sic, "sic_description": None},
        "size": None,
        "mentions_elsewhere": mentions,
    }
    cyber = {
        "topic": "cyber_risk",
        "private": True,
        "signals": [],
        "note": "Private company: it files no 8-K or 10-K, so there are no SEC incident disclosures to check.",
    }
    filing_text = "\n".join(m.get("snippet") or "" for m in mentions.get("mentions") or [])
    return company, cyber, {"text": filing_text}


def build(args: argparse.Namespace) -> dict:
    errors: list[str] = []
    if args.private:
        if not args.sic and not args.sector:
            raise SourceError("A private company needs --sic or --sector, since EDGAR has no industry code for it.")
        company, cyber, filings = private_sections(args, errors)
    else:
        company, cyber, filings = public_sections(args, errors)

    sic_info = company.get("industry") or {}
    sector = industry.sector_by_name(args.sector) if args.sector else None
    if args.sector and sector is None:
        errors.append(f"industry: unknown sector '{args.sector}'. Choose from: {', '.join(industry.sector_names())}.")
    if sector is None:
        sector = industry.sector_for(int(sic_info.get("sic") or 0), sic_info.get("sic_description"))
    industry_section = {**sic_info, **sector}

    # Company names are matched on this machine only; they are never sent to the news source.
    company_terms = [args.company] + split_list(args.aliases)
    if args.no_news:
        news_section = {"topic": "industry_news", "configured": False, "items": [], "note": "Skipped with --no-news."}
    else:
        news_section = run(
            "industry_news", errors, news.search_industry, sector["news_terms"], args.refresh, company_terms
        )

    kev_section = run("kev", errors, kev.exposure, split_list(args.tech), filings["text"], args.refresh)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "company": company,
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
    parser.add_argument("--private", action="store_true", help="The company does not file with the SEC; use its name as given.")
    parser.add_argument("--aliases", help="Comma-separated other names, for example 'Azamara' for 'Azamara Cruises'")
    parser.add_argument("--sic", type=int, help="Industry SIC code for a private company, for example 4400 for cruise lines")
    parser.add_argument("--mention-years", type=int, default=3, help="Years of other companies' filings to search. Default 3.")
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
