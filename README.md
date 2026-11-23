# Target profile lookup

Builds a company and cyber-risk profile from public sources, one module per source:

| Module | Source | Answers | Key |
| --- | --- | --- | --- |
| `scripts/sources/edgar.py` | SEC EDGAR | Who the company is, its industry (SIC) and size, financials, named vendors | None. Set `EDGAR_USER_AGENT` to `"Your Name you@example.com"` |
| `scripts/sources/edgar_cyber.py` | SEC EDGAR | Cyber risk signals: 8-K Item 1.05 incidents, other incident 8-Ks, 10-K cyber passages | Same as above |
| `scripts/sources/news.py` | [CybersecurityNews.us](https://cybersecuritynews.us/developers) | Recent cyber news for the company's industry | `NEWS_API_KEY` |
| `scripts/sources/kev.py` | [CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) | Actively exploited vulnerabilities for vendors the company uses | None |

`scripts/industry.py` maps the SIC code to a CISA sector and the news queries. It is the only link
between EDGAR and the news API, and only those industry queries are sent to the news API.

## Run

```powershell
$env:EDGAR_USER_AGENT = "Your Name you@example.com"
$env:NEWS_API_KEY = "csn_live_..."
py -3 scripts/build_profile.py HTZ --tech "Microsoft,Cisco" > build/profile.json
py -3 scripts/render_report.py build/profile.json site/index.html
```

Options: `--sector "Healthcare and Public Health"` overrides the sector, `--no-news` skips the news API,
`--cyber-years 3` sets how far back to search 8-Ks, `--refresh` ignores the cache.

## Signal levels

- **high**: 8-K Item 1.05, a material cybersecurity incident the company had to disclose.
- **medium**: another 8-K, or a 10-K passage, where the company describes an incident that happened.
  Generic risk-factor language is filtered out.
- **info**: the 10-K states no material incident occurred.

## News API trial limits

The trial allows 1,000 requests a day and 30 a minute for 30 days. Each profile sends at most two
queries (`NEWS_MAX_CALLS`), page 1 only, and caches answers for 24 hours. A 402 or 429 stops further calls.

## GitHub Actions

Add `EDGAR_USER_AGENT` and `NEWS_API_KEY` as repository secrets. Run **Publish target profile** from the
Actions tab with a ticker; the page is published to GitHub Pages.
