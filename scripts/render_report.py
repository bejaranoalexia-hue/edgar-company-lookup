"""Turn EDGAR financials and vendor JSON into one HTML page."""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def money(value: object, unit: str | None) -> str:
    if not isinstance(value, (int, float)):
        return "Not reported"
    if unit == "USD":
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):,.0f}"
    if unit == "USD/shares":
        return f"${value:,.2f}"
    return f"{value:,.2f} {unit or ''}".strip()


def filing_link(accession: object, cik: object) -> str:
    if not accession or not cik:
        return ""
    cik_int = str(int(cik))
    accn = str(accession)
    href = (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik_int}/{accn.replace('-', '')}/{accn}-index.html"
    )
    return f'<a href="{esc(href)}">{esc(accn)}</a>'


def statement_table(lines: list[dict]) -> str:
    rows = []
    for line in lines:
        rows.append(
            "<tr>"
            f"<td>{esc(line.get('label'))}</td>"
            f"<td class=\"num\">{esc(money(line.get('value'), line.get('unit')))}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan=\"2\">No lines returned.</td></tr>"
    return (
        "<table>"
        "<thead><tr><th>Line</th><th>Amount</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def error_block(title: str, payload: dict) -> str:
    return (
        f"<section><h2>{esc(title)}</h2>"
        f"<p class=\"error\">{esc(payload.get('error') or 'No data returned.')}</p></section>"
    )


def financials_html(payload: dict) -> str:
    if payload.get("error"):
        return error_block("Financial statements", payload)
    company = payload.get("company") or {}
    blocks = []
    for period in payload.get("statements") or []:
        link = filing_link(period.get("accession"), company.get("cik"))
        blocks.append(
            "<section>"
            f"<h2>Annual statements for {esc(period.get('period_end'))}</h2>"
            "<p class=\"meta\">"
            f"{esc(period.get('form'))} filed {esc(period.get('filed'))}. Accession {link}."
            "</p>"
            "<h3>Income statement</h3>"
            f"{statement_table(period.get('income_statement') or [])}"
            "<h3>Balance sheet</h3>"
            f"{statement_table(period.get('balance_sheet') or [])}"
            "<h3>Cash flow</h3>"
            f"{statement_table(period.get('cash_flow') or [])}"
            "</section>"
        )
    return "\n".join(blocks) or error_block("Financial statements", {"error": "No statements returned."})


def vendors_html(payload: dict) -> str:
    if payload.get("error"):
        return error_block("Vendors", payload)
    mentions = payload.get("mentions") or []
    if not mentions:
        note = payload.get("note") or "No supplier or vendor passages found."
        return f"<section><h2>Vendors</h2><p>{esc(note)}</p></section>"
    items = []
    for mention in mentions:
        named = "Names a company in this passage." if mention.get("names_a_party") else "Does not name a company."
        url = mention.get("filing_url") or ""
        link = f'<a href="{esc(url)}">Filing text</a>' if url else ""
        items.append(
            "<blockquote>"
            f"<p>{esc(mention.get('snippet'))}</p>"
            "<p class=\"meta\">"
            f"{esc(mention.get('form'))} filed {esc(mention.get('filed'))}. {esc(named)} {link}"
            "</p></blockquote>"
        )
    return "<section><h2>Vendors</h2>\n" + "\n".join(items) + "</section>"


def render(financials: dict, vendors: dict) -> str:
    company = financials.get("company") or vendors.get("company") or {}
    name = company.get("name") or "Company"
    ticker = company.get("ticker") or ""
    title = f"{name} ({ticker})" if ticker else str(name)
    facts_url = ((financials.get("source") or {}).get("endpoint")) or ""
    search_url = ((vendors.get("source") or {}).get("endpoint")) or ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} EDGAR report</title>
  <style>
    body {{
      margin: 0 auto;
      max-width: 920px;
      padding: 2rem 1.25rem 4rem;
      background: #f4f1ea;
      color: #1c1915;
      font-family: Georgia, "Iowan Old Style", serif;
      line-height: 1.45;
    }}
    h1 {{ font-size: 2rem; margin-bottom: 0.25rem; }}
    h2 {{ margin-top: 2rem; font-size: 1.35rem; }}
    h3 {{ margin: 1.25rem 0 0.4rem; font-size: 1.05rem; }}
    a {{ color: #6d4318; }}
    .meta, footer {{ color: #5c564c; font-family: "Segoe UI", sans-serif; font-size: 0.92rem; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #fff;
      font-family: "Segoe UI", sans-serif;
    }}
    th, td {{ padding: 0.5rem 0.7rem; border-bottom: 1px solid #e6e0d4; text-align: left; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    blockquote {{
      margin: 0 0 0.9rem;
      padding: 0.8rem 1rem;
      background: #fff;
      border-left: 3px solid #8a5a2b;
    }}
    .error {{ color: #8a1f1f; }}
  </style>
</head>
<body>
  <header>
    <h1>{esc(title)}</h1>
    <p class="meta">SEC EDGAR annual financial statements and 10-K vendor disclosures. CIK {esc(company.get("cik"))}.</p>
  </header>
  {financials_html(financials)}
  {vendors_html(vendors)}
  <footer>
    <p>Financials source: <a href="{esc(facts_url)}">{esc(facts_url)}</a></p>
    <p>Vendor search: <a href="{esc(search_url)}">{esc(search_url)}</a></p>
  </footer>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print("Usage: render_report.py financials.json vendors.json site/index.html", file=sys.stderr)
        return 1
    page = render(load_json(args[0]), load_json(args[1]))
    destination = Path(args[2])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
