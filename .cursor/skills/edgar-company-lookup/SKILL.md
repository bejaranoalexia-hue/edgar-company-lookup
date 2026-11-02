---
name: edgar-company-lookup
description: >-
  Pulls SEC EDGAR financial statements or vendor disclosures for one public
  company by running scripts/edgar_lookup.py. Use when the user mentions
  EDGAR, SEC filings, 10-K financial statements, suppliers, or vendors.
---

# EDGAR company lookup

This agent answers only two topics. Both come from `scripts/edgar_lookup.py`. Do not call SEC endpoints any other way, and do not answer company profiles, insider filings, 8-K events, or news.

## Pick the topic

- Financial statements, 10-K numbers, income statement, balance sheet, or cash flow: `financials`
- Suppliers, vendors, or supply agreements: `vendors`
- The user asks for both: run the script twice, once per topic.
- The user asks for anything else: say this agent only covers financial statements and vendor disclosures, and ask which of the two to run.

## Run

From the project root:

```bash
py -3 scripts/edgar_lookup.py financials TICKER
py -3 scripts/edgar_lookup.py vendors TICKER
```

`TICKER` may be a ticker or a company name. Add `--years N` to return more annual statements or to read that many latest 10-Ks. The default is 1.

Read the JSON on stdout. If the process exits non-zero, report the `error` field.

Set `EDGAR_USER_AGENT` to a real name and email before calling SEC if the script returns HTTP 403.

## How to answer

Financials: give the income statement, balance sheet, and cash flow from `statements`. For every number include the value, unit, period end, form, and accession number. If a line has `missing: true`, say that line was not in the filing. Do not calculate a missing line.

Vendors: quote each `snippet` and include `filing_url`, form, and filed date. A mention is a disclosure in the 10-K, not a confirmed vendor list. If `mentions` is empty, say the 10-K search did not name suppliers or vendors.
