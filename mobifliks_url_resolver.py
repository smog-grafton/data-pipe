#!/usr/bin/env python3
"""
Resolve a Mobifliks movie detail URL into a direct download URL.

The script:
1) Parses `vid_name` from a supported `downloadvideo*.php` URL.
2) Generates ranked candidate direct-download filenames.
3) Confirms candidates over HTTP and only accepts status 200.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


DIRECT_BASE = "https://mobifliks.info/downloadmp4.php?file="
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 1
DETAIL_PATH_PATTERN = re.compile(r"/downloadvideo(?:\d+)?\.php$")


@dataclass
class ParsedMovie:
    title: str
    year: Optional[str]
    vj_name: Optional[str]
    language: Optional[str]
    raw_vid_name: str


def validate_detail_url(detail_url: str) -> None:
    parsed = urllib.parse.urlparse(detail_url)
    host = (parsed.netloc or "").lower()
    valid_hosts = {"www.mobifliks.com", "mobifliks.com"}
    if host not in valid_hosts:
        raise ValueError("URL host must be mobifliks.com or www.mobifliks.com.")
    if not DETAIL_PATH_PATTERN.search(parsed.path):
        raise ValueError("URL path must end with /downloadvideo.php or /downloadvideo<number>.php.")


def parse_detail_url(detail_url: str) -> ParsedMovie:
    validate_detail_url(detail_url)
    parsed = urllib.parse.urlparse(detail_url)
    query = urllib.parse.parse_qs(parsed.query)
    vid_name = query.get("vid_name", [None])[0]
    if not vid_name:
        raise ValueError("Missing vid_name query parameter in the provided URL.")

    decoded = urllib.parse.unquote_plus(vid_name).strip()
    title, inner = split_title_and_parentheses(decoded)

    year = None
    vj_name = None
    language = None

    if inner:
        year_match = re.search(r"\b(19|20)\d{2}\b", inner)
        if year_match:
            year = year_match.group(0)

        vj_match = re.search(r"\bVJ\s+([^-\)]+)", inner, flags=re.IGNORECASE)
        if vj_match:
            vj_name = clean_token(vj_match.group(1))

        lang_match = re.search(r"\b(Luganda|English|Swahili|Kiswahili)\b", inner, flags=re.IGNORECASE)
        if lang_match:
            language = normalize_language(lang_match.group(1))

    if not language and re.search(r"\bLuganda\b", decoded, flags=re.IGNORECASE):
        language = "luganda"

    return ParsedMovie(
        title=clean_token(title),
        year=year,
        vj_name=vj_name,
        language=language,
        raw_vid_name=decoded,
    )


def split_title_and_parentheses(value: str) -> Tuple[str, Optional[str]]:
    # Capture the last (...) group as metadata if present.
    match = re.match(r"^(.*?)\s*\((.*)\)\s*$", value)
    if not match:
        return value, None
    return match.group(1).strip(), match.group(2).strip()


def normalize_language(value: str) -> str:
    val = value.strip().lower()
    if val in {"luganda"}:
        return "luganda"
    return val


def clean_token(value: str) -> str:
    # Collapse spaces and trim trailing separators.
    return re.sub(r"\s+", " ", value).strip(" -")


def title_variants(title: str) -> List[str]:
    variants = [clean_token(title)]
    filename_safe = clean_token(re.sub(r'[\\/:*?"<>|]+', "-", title))
    if filename_safe:
        variants.append(filename_safe)

    no_punctuation = clean_token(re.sub(r'[\\/:*?"<>|]+', " ", title))
    if no_punctuation:
        variants.append(no_punctuation)

    unique: Dict[str, None] = {}
    for item in variants:
        if item:
            unique[item] = None
    return list(unique.keys())


def vj_variants(vj_name: Optional[str]) -> List[str]:
    if not vj_name:
        return []
    cleaned = clean_token(vj_name)
    return [f"Vj {cleaned}", f"VJ {cleaned}"]


def build_candidates(movie: ParsedMovie) -> List[str]:
    folder = movie.language or "luganda"
    names: List[str] = []
    vj_names = vj_variants(movie.vj_name)
    titles = title_variants(movie.title)

    # Most common direct pattern.
    for title in titles:
        for vj in vj_names:
            names.append(f"{title} by {vj} - Mobifliks.com.mp4")

    # Sometimes year appears in direct filename.
    if movie.year:
        for title in titles:
            for vj in vj_names:
                names.append(f"{title} ({movie.year}) by {vj} - Mobifliks.com.mp4")

    # Fallback to using raw metadata phrase from detail page.
    if vj_names and movie.year and movie.language:
        names.append(
            f"{titles[0]} ({movie.year} - {vj_names[0]} - {movie.language.capitalize()}) - Mobifliks.com.mp4"
        )

    # Deduplicate while preserving order.
    unique: Dict[str, None] = {}
    for item in names:
        unique[item] = None

    return [f"{folder}/{name}" for name in unique.keys()]


def as_direct_url(file_path: str) -> str:
    encoded = urllib.parse.quote(file_path, safe="/")
    return f"{DIRECT_BASE}{encoded}"


def get_status_code(url: str, timeout: int, method: str) -> Optional[int]:
    request = urllib.request.Request(
        url=url,
        method=method,
        headers={
            "User-Agent": "narabox-data-pipe/1.0 (+mobifliks-url-resolver)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def resolve_download_url(
    detail_url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Dict[str, object]:
    movie = parse_detail_url(detail_url)
    candidate_files = build_candidates(movie)

    checked: List[Dict[str, object]] = []
    for file_path in candidate_files:
        url = as_direct_url(file_path)
        attempts = 0
        status_code: Optional[int] = None
        methods_used: List[str] = []
        max_attempts = max(1, retries + 1)

        while attempts < max_attempts and status_code != 200:
            attempts += 1

            head_code = get_status_code(url, timeout=timeout, method="HEAD")
            methods_used.append("HEAD")
            status_code = head_code

            # Some servers may not handle HEAD reliably; fallback to GET.
            if status_code != 200:
                get_code = get_status_code(url, timeout=timeout, method="GET")
                methods_used.append("GET")
                status_code = get_code

            if status_code == 200:
                break

            if attempts < max_attempts:
                time.sleep(0.5)

        accepted = status_code == 200

        checked.append(
            {
                "file": file_path,
                "url": url,
                "status": status_code,
                "attempts": attempts,
                "methods": methods_used,
                "accepted": accepted,
            }
        )

        if accepted:
            return {
                "ok": True,
                "download_url": url,
                "status_code": status_code,
                "parsed": movie.__dict__,
                "checks": checked,
            }

    return {
        "ok": False,
        "download_url": None,
        "status_code": None,
        "parsed": movie.__dict__,
        "checks": checked,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and validate Mobifliks direct download URL from movie detail URL."
    )
    parser.add_argument("url", help="Mobifliks detail URL (downloadvideo.php?... or downloadvideo2.php?...).")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Retries per candidate after first attempt (default: {DEFAULT_RETRIES}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON output instead of concise text.",
    )
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
            print("Try increasing timeout or inspecting candidate patterns with --json.")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
