#!/usr/bin/env python3
"""
Resolve a Namzentertainment prev.php URL into a direct download URL.

Namzentertainment (https://namzentertainment.com/) uses prev.php?id=... pages that
embed or link to direct downloads on namzentertainments.xyz (e.g. NEW PRO/Title.Year.720 VJ X@katflix..mp4).

This resolver:
1) Fetches the prev.php page and extracts any direct .mp4/.mkv links (or namzentertainments.xyz URLs).
2) Validates the first candidate with HTTP HEAD/GET and returns 200 OK URL.
3) If no link is found in the page, falls back to Ugaflix-style candidate generation for namzentertainments.xyz.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

USER_AGENT = "data-pipe/1.0 (+namz-url-resolver)"
DEFAULT_TIMEOUT = 20
PER_REQUEST_TIMEOUT_CAP = 5


def validate_prev_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host not in ("namzentertainment.com", "namzentertainments.com"):
        raise ValueError("URL host must be namzentertainment.com or namzentertainments.com.")
    path = (parsed.path or "").strip("/")
    if "prev.php" not in path and "prev" not in path.lower():
        raise ValueError("URL must be a prev.php (or similar) page.")
    if "id=" not in (parsed.query or ""):
        raise ValueError("URL must include id= query parameter.")


def fetch_page(url: str, timeout: int) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=min(timeout, PER_REQUEST_TIMEOUT_CAP)) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def extract_download_candidates(html: str, page_url: str) -> List[str]:
    """Extract direct download URLs from prev.php HTML: video src, href to .mp4/.mkv, namzentertainments.xyz."""
    base = urllib.parse.urljoin(page_url, "/")
    candidates: List[str] = []
    seen: Dict[str, None] = {}

    # href or src containing .mp4, .mkv, or namzentertainments.xyz
    for pattern in (
        r'(?:href|src)=["\']([^"\']*\.(?:mp4|mkv)[^"\']*)["\']',
        r'(?:href|src)=["\']([^"\']*namzentertainments?\.xyz[^"\']*)["\']',
        r'url\s*[=:]\s*["\']([^"\']*\.(?:mp4|mkv)[^"\']*)["\']',
        r'["\'](https?://[^"\']*namzentertainments?\.xyz[^"\']*(?:\.mp4|\.mkv)?[^"\']*)["\']',
        r'["\'](https?://[^"\']*\.(?:mp4|mkv))["\']',
    ):
        for m in re.finditer(pattern, html, re.IGNORECASE):
            raw = m.group(1).strip()
            if not raw or raw.startswith("data:"):
                continue
            url = raw if raw.startswith("http") else urllib.parse.urljoin(base, raw)
            url = url.split("#")[0].split("?")[0]
            if url not in seen and (".mp4" in url.lower() or ".mkv" in url.lower() or "namzentertainments" in url.lower()):
                seen[url] = None
                candidates.append(url)
    return candidates


def get_status_code(url: str, timeout: int, method: str) -> Optional[int]:
    req = urllib.request.Request(
        url=url,
        method=method,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def resolve_download_url(
    prev_url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 1,
) -> Dict[str, object]:
    """
    Resolve namzentertainment.com/prev.php?id=... to a validated direct download URL.
    If the page contains no extractable link, falls back to Ugaflix candidate generation
    using the page title or a generic slug derived from the URL.
    """
    validate_prev_url(prev_url)
    per_request = min(timeout, PER_REQUEST_TIMEOUT_CAP)
    checked: List[Dict[str, object]] = []

    # 1) Fetch prev.php and extract links
    html = fetch_page(prev_url, timeout)
    if html:
        candidates = extract_download_candidates(html, prev_url)
    else:
        candidates = []

    # 2) Validate each candidate
    for url in candidates:
        status_code = get_status_code(url, per_request, "HEAD")
        if status_code != 200:
            status_code = get_status_code(url, per_request, "GET")
        checked.append({
            "url": url,
            "status": status_code,
            "attempts": 1,
            "methods": ["HEAD", "GET"] if status_code == 200 else ["HEAD", "GET"],
            "accepted": status_code == 200,
        })
        if status_code == 200:
            return {
                "ok": True,
                "download_url": url,
                "status_code": status_code,
                "parsed": {"prev_url": prev_url, "source": "page_link"},
                "checks": checked,
            }

    # 3) Fallback: try Ugaflix-style namz/pearlpix candidate generation if we have a way to get title
    try:
        from ugaflix_url_resolver import (
            ParsedMovie,
            build_candidates,
            resolve_from_parsed_movie,
            scrape_title_from_page,
            slug_to_title,
        )
    except ImportError:
        return {
            "ok": False,
            "download_url": None,
            "status_code": None,
            "parsed": {"prev_url": prev_url, "source": "page_link"},
            "checks": checked,
        }

    # Build a minimal ParsedMovie from prev URL (id only; slug/title from page or generic)
    parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(prev_url).query)
    id_val = (parsed_qs.get("id") or [None])[0]
    slug = f"movie-{id_val}" if id_val else "movie"
    title_from_slug = slug_to_title(slug)
    movie = ParsedMovie(
        slug=slug,
        post_id=id_val,
        title_from_slug=title_from_slug,
        title_from_page=None,
        raw_url=prev_url,
    )
    movie.title_from_page = scrape_title_from_page(prev_url, timeout)
    result = resolve_from_parsed_movie(movie, timeout=timeout, retries=retries)
    if result.get("ok"):
        result["parsed"] = {"prev_url": prev_url, "source": "candidate_fallback", **result.get("parsed", {})}
        result["checks"] = checked + (result.get("checks") or [])
    else:
        result["parsed"] = {"prev_url": prev_url, "source": "page_link"}
        result["checks"] = checked
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve Namzentertainment prev.php URL to validated direct download URL.",
    )
    parser.add_argument("url", help="Namz prev URL (e.g. https://namzentertainment.com/prev.php?id=8857).")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT}).")
    parser.add_argument("--retries", type=int, default=1, help="Retries per candidate (fallback only).")
    parser.add_argument("--json", action="store_true", help="Print full JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = resolve_download_url(args.url, timeout=args.timeout, retries=args.retries)
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["ok"]:
            print("Resolved direct download URL:")
            print(result["download_url"])
            print("Validation status: 200 OK")
        else:
            print("No validated (200 OK) direct download URL found.")
            for c in (result.get("checks") or [])[:10]:
                print(" ", c.get("url"), "->", c.get("status"))

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
