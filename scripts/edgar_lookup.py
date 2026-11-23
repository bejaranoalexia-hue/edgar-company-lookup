"""Single-topic EDGAR lookups, kept for quick checks from the command line.

    py -3 scripts/edgar_lookup.py financials AAPL
    py -3 scripts/edgar_lookup.py vendors AAPL
    py -3 scripts/edgar_lookup.py overview AAPL
    py -3 scripts/edgar_lookup.py cyber AAPL

The code lives in scripts/sources/. For the full profile use build_profile.py.
Set EDGAR_USER_AGENT to "Your Name you@example.com".
"""

from __future__ import annotations

import argparse
import json
import sys

from common import SourceError
from sources import edgar, edgar_cyber


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Pull one EDGAR topic for one company.")
    parser.add_argument("topic", choices=("financials", "vendors", "overview", "cyber"))
    parser.add_argument("company", help="Ticker or company name, for example AAPL or Apple")
    parser.add_argument("--years", type=int, default=1, help="Annual periods or 10-Ks to read. Default 1.")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached SEC responses.")
    args = parser.parse_args(argv)
    try:
        company = edgar.resolve_company(args.company, edgar.load_companies(args.refresh))
        if args.topic == "financials":
            result = edgar.financials(company, args.years, args.refresh)
        elif args.topic == "vendors":
            result = edgar.search_vendors(company, args.years, args.refresh)
        elif args.topic == "overview":
            result = edgar.overview(company, args.refresh)
        else:
            result = edgar_cyber.risk_signals(company, max(args.years, 3), args.refresh)
    except SourceError as exc:
        json.dump({"error": str(exc)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
