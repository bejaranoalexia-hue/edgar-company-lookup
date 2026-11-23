"""HTTP and cache helpers shared by every data source.

Responses are cached on disk under scripts/.cache so repeat runs do not
spend API quota. Cache files are named from a hash of the URL only, so
API keys sent in headers never end up in a file name.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / ".cache"
DEFAULT_TTL_SECONDS = 12 * 60 * 60


class SourceError(Exception):
    """A lookup failed. The message is safe to show in the report."""


def _cache_path(url: str, suffix: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}{suffix}"


RETRY_STATUS = {500, 502, 503, 504}
RETRY_DELAYS = (1.5, 4.0)


def http_get(url: str, headers: dict[str, str], label: str) -> str:
    """GET with two retries on server errors (SEC full-text search fails intermittently).

    Rate-limit (429) and quota (402) errors are not retried, so a news API
    trial is never charged twice for one query.
    """
    request = urllib.request.Request(url, headers=headers)
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUS and attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
                continue
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise SourceError(f"{label} returned HTTP {exc.code} for {url}. {detail}".strip()) from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"Could not reach {label} at {url}. {exc.reason}") from exc
    raise SourceError(f"{label} kept failing for {url}.")


def cached_json(
    url: str,
    refresh: bool,
    headers: dict[str, str],
    label: str,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> dict | list:
    path = _cache_path(url, ".json")
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text(encoding="utf-8"))
    text = http_get(url, {**headers, "Accept": "application/json"}, label)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceError(f"{label} returned non-JSON for {url}.") from exc
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def cached_text(
    url: str,
    refresh: bool,
    headers: dict[str, str],
    label: str,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> str:
    path = _cache_path(url, ".html")
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < ttl:
        return path.read_text(encoding="utf-8")
    text = http_get(url, {**headers, "Accept": "text/html"}, label)
    path.write_text(text, encoding="utf-8")
    return text
