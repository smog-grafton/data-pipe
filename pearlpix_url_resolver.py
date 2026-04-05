#!/usr/bin/env python3
"""
Resolve a Pearl Pix movie detail URL into a direct download URL.

Pearl Pix (https://www.pearlpix.net/) uses the same CDN as Ugaflix for many files:
- jimmy.pearlpix.xyz (primary)
- namzentertainments.xyz (fallback)

URL shape: https://www.pearlpix.net/movies/details/<slug>/<id>
Example: https://www.pearlpix.net/movies/details/annabelle-creation-vj-junior/3662
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from typing import Dict, Optional

from ugaflix_url_resolver import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    ParsedMovie,
    resolve_from_parsed_movie,
    scrape_title_from_page,
    slug_to_title,
)


def validate_pearlpix_url(detail_url: str) -> None:
    parsed = urllib.parse.urlparse(detail_url)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != "pearlpix.net":
        raise ValueError("URL host must be pearlpix.net or www.pearlpix.net.")
    path = (parsed.path or "").strip("/")
    if not path.startswith("movies/details/"):
        raise ValueError("URL path must be like /movies/details/<slug>/<id>.")


def parse_pearlpix_url(detail_url: str) -> ParsedMovie:
    validate_pearlpix_url(detail_url)
    parsed = urllib.parse.urlparse(detail_url)
    path = (parsed.path or "").strip("/")
    parts = path.split("/")
    if len(parts) < 4:
        raise ValueError("URL path must be /movies/details/<slug>/<id>.")
    slug = parts[2]
    post_id = parts[3] if len(parts) > 3 and parts[3].isdigit() else None
    title_from_slug = slug_to_title(slug)
    return ParsedMovie(
        slug=slug,
        post_id=post_id,
        title_from_slug=title_from_slug,
        raw_url=detail_url,
    )


def resolve_download_url(
    detail_url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    fetch_page: bool = True,
) -> Dict[str, object]:
    movie = parse_pearlpix_url(detail_url)
    if fetch_page and movie.raw_url:
        movie.title_from_page = scrape_title_from_page(movie.raw_url, timeout)
    return resolve_from_parsed_movie(movie, timeout=timeout, retries=retries)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve Pearl Pix movie detail URL to validated direct download URL (jimmy.pearlpix.xyz / namzentertainments.xyz)."
    )
    parser.add_argument("url", help="Pearl Pix detail URL (e.g. https://www.pearlpix.net/movies/details/...).")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT}).")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Retries per candidate.")
    parser.add_argument("--no-fetch-page", action="store_true", help="Do not fetch the detail page to scrape title.")
    parser.add_argument("--json", action="store_true", help="Print full JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = resolve_download_url(
            args.url,
            timeout=args.timeout,
            retries=args.retries,
            fetch_page=not args.no_fetch_page,
        )
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
            print("Checked candidates (use --json to see full list):")
            for c in (result.get("checks") or [])[:10]:
                print(" ", c.get("url"), "->", c.get("status"))

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
