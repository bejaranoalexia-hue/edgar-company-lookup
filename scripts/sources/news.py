"""CybersecurityNews.us: recent cyber news that matches one industry.

How it saves API calls:
    * It fetches a few incident-focused categories (CATEGORIES), at most
      PAGES_PER_CATEGORY pages of 50 stories each. These requests do not
      depend on the company or industry, so they are cached for 24 hours and
      shared by every profile built that day: at most 6 calls a day in total.
    * Industry filtering happens on this machine, by matching the sector's
      terms from industry.py against each story's headline and summaries.
      The company name and contacts are never sent.
    * A 401, 402 or 429 response stops further calls immediately.

Endpoint notes (checked 2026-11-23):
    The developer docs list https://api.cybersecuritynews.us/v1, but that
    host does not resolve. The site's own API at
    https://cybersecuritynews.us/api/v1/news works, filters by `category`,
    returns `items` with `headline`, `quickSummary`, `publishedAt`, and
    caps pageSize at 50. Its /search endpoint currently returns HTTP 500.
    Set NEWS_API_BASE to switch hosts if the keyed Data API comes online.
    NEWS_API_KEY is sent as X-API-Key when set; the public endpoint does
    not require it.
"""

from __future__ import annotations

import os
import re
import urllib.parse

from common import SourceError, cached_json

API_BASE = (os.environ.get("NEWS_API_BASE") or "https://cybersecuritynews.us/api/v1").rstrip("/")
LABEL = "CybersecurityNews.us"
CACHE_TTL_SECONDS = 24 * 60 * 60
CATEGORIES = ("data-breaches", "ransomware", "threats-breaches")
PAGES_PER_CATEGORY = 2
PAGE_SIZE = 50
MAX_CALLS = int(os.environ.get("NEWS_MAX_CALLS") or len(CATEGORIES) * PAGES_PER_CATEGORY)
MAX_RESULTS = 10
STOP_CODES = ("HTTP 401", "HTTP 402", "HTTP 429")


def headers() -> dict[str, str]:
    result = {"User-Agent": "EdgarCompanyLookup"}
    api_key = os.environ.get("NEWS_API_KEY")
    if api_key:
        result["X-API-Key"] = api_key
    return result


def fetch_stories(refresh: bool) -> tuple[list[dict], list[str], int]:
    """All stories from CATEGORIES, newest pages first. Returns stories, errors, calls made."""
    stories: dict[object, dict] = {}
    errors: list[str] = []
    calls = 0
    for category in CATEGORIES:
        for page in range(1, PAGES_PER_CATEGORY + 1):
            if calls >= MAX_CALLS:
                return list(stories.values()), errors, calls
            query = urllib.parse.urlencode({"category": category, "page": page, "pageSize": PAGE_SIZE})
            url = f"{API_BASE}/news?{query}"
            calls += 1
            try:
                payload = cached_json(url, refresh, headers(), LABEL, ttl=CACHE_TTL_SECONDS)
            except SourceError as exc:
                errors.append(str(exc))
                if any(code in str(exc) for code in STOP_CODES):
                    return list(stories.values()), errors, calls
                break
            items = (payload.get("items") or payload.get("data") or []) if isinstance(payload, dict) else payload
            for item in items:
                if isinstance(item, dict):
                    stories.setdefault(item.get("id") or item.get("slug"), {**item, "_category": category})
            total = payload.get("total") if isinstance(payload, dict) else None
            if len(items) < PAGE_SIZE or (isinstance(total, int) and page * PAGE_SIZE >= total):
                break
    return list(stories.values()), errors, calls


def term_pattern(terms: list[str]) -> re.Pattern[str] | None:
    # Short all-caps terms (OT, ICS, POS, MSP) match case-sensitively to avoid "pot", "ics".
    parts = []
    for term in terms:
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        parts.append(f"(?-i:{escaped})" if term.isupper() and len(term) <= 5 else escaped)
    if not parts:
        return None
    return re.compile(r"(?<![\w-])(?:" + "|".join(parts) + r")(?![\w-])", re.IGNORECASE)


def story_text(story: dict) -> str:
    fields = (story.get("headline"), story.get("title"), story.get("quickSummary"), story.get("whyItMatters"))
    return " ".join(str(value) for value in fields if value)


def story_url(story: dict) -> str | None:
    slug = story.get("slug")
    if slug:
        return f"https://cybersecuritynews.us/news/{slug}"
    previews = story.get("sourcesPreview") or []
    return previews[0].get("url") if previews else story.get("url")


def search_industry(terms: list[str], refresh: bool, company_terms: list[str] | None = None) -> dict:
    """Stories matching the industry terms, plus any that name the company.

    company_terms are matched locally like industry terms; they are never
    sent to the API. Stories naming the company are listed first.
    """
    pattern = term_pattern(terms)
    company_pattern = term_pattern([t for t in company_terms or [] if len(t) >= 4])
    if pattern is None and company_pattern is None:
        return {"topic": "industry_news", "configured": True, "terms": [], "items": [],
                "note": "No industry terms to match, so news was skipped."}

    stories, errors, calls = fetch_stories(refresh)
    if errors and not stories:
        raise SourceError("; ".join(errors))

    matched = []
    for story in stories:
        text = story_text(story)
        hits = sorted({m.group(0).lower() for m in pattern.finditer(text)}) if pattern else []
        names_company = bool(company_pattern and company_pattern.search(text))
        if not hits and not names_company:
            continue
        source = story.get("primarySource") or {}
        matched.append(
            {
                "title": story.get("headline") or story.get("title"),
                "url": story_url(story),
                "date": story.get("publishedAt"),
                "summary": story.get("quickSummary"),
                "severity": story.get("severity"),
                "category": (story.get("primaryCategory") or {}).get("name") or story["_category"],
                "source": source.get("name"),
                "cves": [
                    cve if isinstance(cve, str) else str(cve.get("cveId") or cve.get("id") or "")
                    for cve in story.get("cves") or []
                    if isinstance(cve, (str, dict))
                ],
                "kev": bool(story.get("kev")),
                "matched_terms": hits,
                "names_company": names_company,
            }
        )
    matched.sort(
        key=lambda item: (item["names_company"], len(item["matched_terms"]), str(item["date"] or "")), reverse=True
    )
    matched = sorted(
        matched[:MAX_RESULTS], key=lambda item: (item["names_company"], str(item["date"] or "")), reverse=True
    )
    dates = sorted(str(s.get("publishedAt") or "") for s in stories if s.get("publishedAt"))
    return {
        "topic": "industry_news",
        "configured": True,
        "terms": terms,
        "categories": list(CATEGORIES),
        "stories_scanned": len(stories),
        "window": {"oldest": dates[0][:10], "newest": dates[-1][:10]} if dates else None,
        "pages_read": calls,  # cached pages count here too; repeat runs the same day send nothing
        "items": matched,
        "company_mentions": sum(1 for item in matched if item["names_company"]),
        "errors": errors,
        "note": None if matched else "No recent stories in these categories matched the industry terms.",
    }
