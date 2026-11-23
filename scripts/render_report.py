"""Turn build_profile.py JSON into one HTML page.

    py -3 scripts/render_report.py build/profile.json site/index.html
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def link(url: object, text: object) -> str:
    return f'<a href="{esc(url)}">{esc(text)}</a>' if url else esc(text)


def money(value: object, unit: str | None = "USD") -> str:
    if not isinstance(value, (int, float)):
        return "Not reported"
    if unit == "USD":
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):,.0f}"
    if unit == "USD/shares":
        return f"${value:,.2f}"
    return f"{value:,.2f} {unit or ''}".strip()


def table(rows: list[tuple[str, str]], head: tuple[str, str] | None = None, num: bool = False) -> str:
    cls = ' class="num"' if num else ""
    body = "".join(f"<tr><td>{esc(k)}</td><td{cls}>{v}</td></tr>" for k, v in rows)
    thead = f"<thead><tr><th>{esc(head[0])}</th><th>{esc(head[1])}</th></tr></thead>" if head else ""
    return f"<table>{thead}<tbody>{body}</tbody></table>"


def error_or_note(payload: dict) -> str:
    if payload.get("error"):
        return f'<p class="error">{esc(payload["error"])}</p>'
    return f"<p>{esc(payload.get('note'))}</p>" if payload.get("note") else ""


def employees(item: object) -> str:
    if not isinstance(item, dict) or not isinstance(item.get("value"), (int, float)):
        return "Not reported"
    source = link(item.get("url"), f"{item.get('form') or '10-K'} filed {item.get('as_of')}")
    quote = f'<br><span class="meta">"{esc(item["quote"])}"</span>' if item.get("quote") else ""
    return f"<strong>{item['value']:,}</strong> ({source}){quote}"


# ---------------------------------------------------------------- sections


def industry_rows(industry: dict) -> list[tuple[str, str]]:
    sic = f"{industry.get('sic') or 'Not given'} {industry.get('sic_description') or ''}".strip()
    return [
        ("SIC code", esc(sic)),
        ("CISA sector", esc(industry.get("cisa_sector") or "No match, using SIC description")),
        ("News terms", esc(", ".join(industry.get("news_terms") or []))),
    ]


def private_company_html(company: dict, industry: dict) -> str:
    identity = company.get("identity") or {}
    mentions = company.get("mentions_elsewhere") or {}
    aliases = ", ".join(identity.get("aliases") or [])
    rows = [
        ("Status", "Private company. It does not file with the SEC, so there is no employee count, "
                   "income statement or 10-K vendor list."),
        ("Also searched as", esc(aliases or "None")),
    ]
    items = []
    for m in mentions.get("mentions") or []:
        quote = f"<p>{esc(m['snippet'])}</p>" if m.get("snippet") else ""
        items.append(
            f"<blockquote><p><strong>{esc(m.get('filer'))}</strong> "
            f'<span class="meta">{esc(m.get("kind"))}</span></p>{quote}'
            f'<p class="meta">{esc(m.get("form"))} filed {esc(m.get("filed"))}. {link(m.get("url"), "Filing")}</p>'
            "</blockquote>"
        )
    window = mentions.get("window") or {}
    meta = (
        f'<p class="meta">{esc(mentions.get("filers"))} companies mention it in SEC filings from '
        f'{esc(window.get("start"))} to {esc(window.get("end"))}. Newest filing per company:</p>'
        if mentions.get("filers")
        else ""
    )
    return (
        "<section><h2>Who the company is</h2>"
        f"{table(rows)}<h3>Industry</h3>{table(industry_rows(industry))}"
        f"<h3>What other companies' SEC filings say about it</h3>{meta}{''.join(items)}{error_or_note(mentions)}"
        "</section>"
    )


def company_html(company: dict, industry: dict) -> str:
    if company.get("error"):
        return f"<section><h2>Who the company is</h2>{error_or_note(company)}</section>"
    if company.get("private"):
        return private_company_html(company, industry)
    identity = company.get("identity") or {}
    size = company.get("size") or {}
    identity_rows = [
        ("Tickers", esc(", ".join(identity.get("tickers") or []))),
        ("Exchanges", esc(", ".join(sorted(set(identity.get("exchanges") or []))))),
        ("Incorporated in", esc(identity.get("state_of_incorporation"))),
        ("Business address", esc(identity.get("business_address"))),
        ("Phone", esc(identity.get("phone"))),
        ("Website", link(identity.get("website"), identity.get("website")) if identity.get("website") else "Not listed"),
        ("Former names", esc(", ".join(identity.get("former_names") or []) or "None")),
    ]
    size_rows = [
        ("Employees", employees(size.get("employees"))),
        ("Size band", esc(size.get("size_band") or "Unknown")),
        (f"Revenue (FY ending {size.get('fiscal_period_end') or 'n/a'})", esc(money(size.get("revenue")))),
        ("Filer category", esc(size.get("filer_category"))),
    ]
    return (
        "<section><h2>Who the company is</h2>"
        f"{table(identity_rows)}<h3>Industry</h3>{table(industry_rows(industry))}"
        f"<h3>Size</h3>{table(size_rows)}</section>"
    )


def cyber_html(cyber: dict) -> str:
    head = "<section><h2>Cybersecurity risk signals</h2>"
    if cyber.get("error") or cyber.get("private"):
        return head + error_or_note(cyber) + "</section>"
    summary = cyber.get("summary") or {}
    annual = summary.get("latest_10k") or {}
    meta = (
        f"<p class=\"meta\">8-K Item 1.05 material incidents: {esc(summary.get('material_incidents_8k'))}. "
        f"Other 8-Ks mentioning an incident: {esc(summary.get('other_incident_8ks'))}. "
        f"Latest 10-K: {link(annual.get('url'), annual.get('filed') or 'not found')}"
        f"{', has Item 1C Cybersecurity' if annual.get('item_1c_present') else ''}.</p>"
    )
    items = []
    for signal in cyber.get("signals") or []:
        detail = f"<p>{esc(signal['detail'])}</p>" if signal.get("detail") else ""
        items.append(
            f'<div class="signal {esc(signal["level"])}">'
            f'<p><span class="badge">{esc(signal["level"])}</span> <strong>{esc(signal["title"])}</strong></p>'
            f"{detail}"
            f'<p class="meta">{esc(signal.get("form"))} filed {esc(signal.get("filed"))}. '
            f'{link(signal.get("url"), "Filing")}</p></div>'
        )
    errors = "".join(f'<p class="error">{esc(e)}</p>' for e in cyber.get("errors") or [])
    return head + meta + "".join(items) + error_or_note(cyber) + errors + "</section>"


def news_html(news: dict) -> str:
    head = "<section><h2>Industry cyber news</h2>"
    if news.get("error"):
        return head + error_or_note(news) + "</section>"
    window = news.get("window") or {}
    meta = (
        f'<p class="meta">{esc(news.get("stories_scanned"))} recent CybersecurityNews.us stories scanned '
        f'({esc(window.get("oldest"))} to {esc(window.get("newest"))}) for industry terms.</p>'
        if news.get("stories_scanned") is not None
        else ""
    )
    items = []
    for item in news.get("items") or []:
        parts = (
            (item.get("date") or "")[:10],
            item.get("severity"),
            item.get("source"),
            ", ".join(c for c in item.get("cves") or [] if c),
            "names the company" if item.get("names_company") else None,
            "matched: " + ", ".join(item.get("matched_terms") or []) if item.get("matched_terms") else None,
        )
        extra = " · ".join(str(x) for x in parts if x)
        summary = f"<p>{esc(item['summary'])}</p>" if item.get("summary") else ""
        items.append(
            f"<li><strong>{link(item.get('url'), item.get('title'))}</strong>"
            f'<span class="meta"> {esc(extra)}</span>{summary}</li>'
        )
    listing = f"<ul class=\"news\">{''.join(items)}</ul>" if items else ""
    errors = "".join(f'<p class="error">{esc(e)}</p>' for e in news.get("errors") or [])
    return head + meta + listing + error_or_note(news) + errors + "</section>"


def kev_html(kev: dict) -> str:
    head = "<section><h2>Known exploited vulnerabilities (CISA KEV)</h2>"
    if kev.get("error"):
        return head + error_or_note(kev) + "</section>"
    meta = f'<p class="meta">Catalog {esc(kev.get("catalog_version"))}.</p>'
    blocks = []
    for vendor in kev.get("vendors") or []:
        rows = "".join(
            f"<tr><td>{esc(v['cve'])}</td><td>{esc(v['product'])}</td><td>{esc(v['date_added'])}</td>"
            f"<td>{'Yes' if v['ransomware'] else ''}</td></tr>"
            for v in vendor.get("recent") or []
        )
        blocks.append(
            f"<h3>{esc(vendor['vendor'])}</h3>"
            f'<p class="meta">Matched from {esc(vendor["matched_from"])}. {vendor["kev_count"]} KEV entries, '
            f"{vendor['ransomware_count']} used in ransomware. Latest added {esc(vendor['latest_added'])}.</p>"
            "<table><thead><tr><th>CVE</th><th>Product</th><th>Added</th><th>Ransomware</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    unmatched = kev.get("unmatched_names") or []
    missing = f'<p class="meta">Not in KEV: {esc(", ".join(unmatched))}.</p>' if unmatched else ""
    return head + meta + "".join(blocks) + missing + error_or_note(kev) + "</section>"


def financials_html(financials: dict) -> str:
    head = "<section><h2>Latest annual income statement</h2>"
    if financials.get("error"):
        return head + error_or_note(financials) + "</section>"
    blocks = []
    for period in financials.get("statements") or []:
        blocks.append(f'<p class="meta">{esc(period.get("form"))} for the year ending {esc(period.get("period_end"))}, '
                      f"filed {esc(period.get('filed'))}.</p>")
        rows = [(line.get("label"), esc(money(line.get("value"), line.get("unit")))) for line in period.get("income_statement") or []]
        blocks.append(f"<h3>Income statement</h3>{table(rows, ('Line', 'Amount'), num=True)}")
    return head + "".join(blocks) + "</section>"


def vendors_html(vendors: dict) -> str:
    head = "<section><h2>Vendors named in the 10-K</h2>"
    if vendors.get("error") or not vendors.get("vendors"):
        return head + error_or_note(vendors) + "</section>"
    items = "".join(
        f"<li><strong>{esc(v.get('name'))}</strong>: {esc(v.get('service'))}</li>" for v in vendors["vendors"]
    )
    first = vendors["vendors"][0]
    meta = f'<p class="meta">From the {link(first.get("filing_url"), first.get("form") or "10-K")} filed {esc(first.get("filed"))}.</p>'
    return head + f"<ul>{items}</ul>" + meta + "</section>"


STYLE = """
    :root { --bg:#f4f1ea; --fg:#1c1915; --muted:#5c564c; --card:#fff; --line:#e6e0d4; --accent:#6d4318;
            --high:#8a1f1f; --medium:#9a6a00; --info:#3d5a80; }
    body { margin:0 auto; max-width:920px; padding:2rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
           font-family:Georgia,"Iowan Old Style",serif; line-height:1.45; }
    h1 { font-size:2rem; margin-bottom:.25rem; }
    h2 { margin-top:2.2rem; font-size:1.35rem; border-bottom:1px solid var(--line); padding-bottom:.3rem; }
    h3 { margin:1.25rem 0 .4rem; font-size:1.05rem; }
    a { color:var(--accent); }
    .meta, footer { color:var(--muted); font-family:"Segoe UI",sans-serif; font-size:.92rem; }
    table { width:100%; border-collapse:collapse; background:var(--card); font-family:"Segoe UI",sans-serif; }
    th, td { padding:.5rem .7rem; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
    td.num { text-align:right; font-variant-numeric:tabular-nums; }
    blockquote, .signal { margin:0 0 .9rem; padding:.8rem 1rem; background:var(--card); border-left:3px solid #8a5a2b; }
    .signal.high { border-left-color:var(--high); } .signal.medium { border-left-color:var(--medium); }
    .signal.info { border-left-color:var(--info); }
    .badge { text-transform:uppercase; font:600 .75rem "Segoe UI",sans-serif; letter-spacing:.05em; padding:.1rem .4rem;
             border-radius:3px; color:#fff; background:var(--info); }
    .high .badge { background:var(--high); } .medium .badge { background:var(--medium); }
    ul.news { padding-left:1.1rem; } ul.news li { margin-bottom:.8rem; }
    .error { color:var(--high); }
    @media (max-width:600px) { table { font-size:.9rem; } th, td { padding:.4rem; } }
"""


def render(profile: dict) -> str:
    company = profile.get("company") or {}
    identity = company.get("identity") or {}
    basic = company.get("company") or {}
    name = identity.get("name") or basic.get("name") or "Company"
    ticker = basic.get("ticker") or ""
    title = f"{name} ({ticker})" if ticker else str(name)
    errors = "".join(f"<li>{esc(e)}</li>" for e in profile.get("errors") or [])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} target profile</title>
  <style>{STYLE}</style>
</head>
<body>
  <header>
    <h1>{esc(title)}</h1>
    <p class="meta">Company and cyber-risk profile from SEC EDGAR, CybersecurityNews.us and CISA KEV.
    {f'CIK {esc(basic.get("cik"))}.' if basic.get("cik") else "Private company."} Generated {esc(profile.get("generated_at"))}.</p>
  </header>
  {company_html(company, profile.get("industry") or {})}
  {cyber_html(profile.get("cyber_risk") or {})}
  {news_html(profile.get("industry_news") or {})}
  {kev_html(profile.get("kev") or {})}
  {"" if company.get("private") else financials_html(company.get("financials") or {})}
  {"" if company.get("private") else vendors_html(company.get("vendors") or {})}
  <footer>{f"<h2>Lookup errors</h2><ul>{errors}</ul>" if errors else ""}
    <p>Sources: <a href="https://www.sec.gov/edgar/search/">SEC EDGAR</a>,
    <a href="https://cybersecuritynews.us">CybersecurityNews.us</a>,
    <a href="https://www.cisa.gov/known-exploited-vulnerabilities-catalog">CISA KEV</a>.</p>
  </footer>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Usage: render_report.py profile.json site/index.html", file=sys.stderr)
        return 1
    profile = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    if profile.get("error"):
        print(f"Profile has an error, page not written: {profile['error']}", file=sys.stderr)
        return 1
    destination = Path(args[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(profile), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
