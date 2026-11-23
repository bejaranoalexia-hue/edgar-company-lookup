---
name: edgar-company-lookup
description: >-
  Builds a company and cyber-risk profile for one public company from SEC
  EDGAR, CybersecurityNews.us and the CISA KEV catalog. Use when the user
  mentions EDGAR, SEC filings, a company profile, employees, income
  statement, vendors, cyber incidents, industry cyber news, or KEV.
---

# Target profile lookup

All data comes from the scripts in `scripts/`. Do not call the APIs any other way.

## Run

From the project root:

```bash
py -3 scripts/build_profile.py TICKER > build/profile.json
py -3 scripts/render_report.py build/profile.json site/index.html
```

`TICKER` may be a ticker or a company name. Options: `--tech "Microsoft,Cisco"` checks named vendors
against CISA KEV, `--sector "Financial Services"` overrides the industry, `--no-news` skips the news source.

For one EDGAR topic only: `py -3 scripts/edgar_lookup.py {overview|financials|vendors|cyber} TICKER`.

Read the JSON on stdout. If the process exits non-zero, report the `error` field. Section-level failures
are listed in `errors` and inside that section.

Set `EDGAR_USER_AGENT` to a real name and email before calling SEC if the script returns HTTP 403.
The company must file with the SEC; private companies are not in EDGAR.

## How to answer

- Company: `company.identity`, `company.industry`, and `company.size.employees` (quote `quote` and give the
  10-K it came from). Revenue is only used for the size band.
- Income statement: from `company.financials.statements[].income_statement`. If a line has
  `missing: true`, say it was not in the filing. Do not calculate a missing line.
- Vendors: list `company.vendors.vendors` as bullets of `name`: `service`. These are vendors the 10-K names,
  not a full vendor list. If empty, say the 10-K does not name specific vendors.
- Cyber risk: list `cyber_risk.signals` by `level` (high, medium, info) with the filing link.
- News and KEV: summarize `industry_news.items` and `kev.vendors`, and say which terms or vendors matched.
