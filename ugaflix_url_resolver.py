#!/usr/bin/env python3
"""
Resolve a Ugaflix movie detail URL into a direct download URL.

Ugaflix (https://ugaflix.com/) links to downloads on:
- namzentertainments.xyz (relord2)
- jimmy.pearlpix.xyz (Pearl Pix CDN: FEB 2026/WEEK 4/Title__vj name 2026.mp4, etc.)
This resolver:
1) Parses the detail URL (slug + optional id) and optionally scrapes the page for title.
2) Generates ranked candidate direct-download URLs using multiple naming algorithms.
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
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

NAMZENT_BASE = "https://namzentertainments.xyz/projects/relord2/"
NAMZENT_ROOT = "https://namzentertainments.xyz/"  # for NEW PRO/, etc.
PEARLPIX_JIMMY_BASE = "https://jimmy.pearlpix.xyz/"
PEARLPIX_JIM_BASE = "https://jim.pearlpix.xyz/"
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 1
USER_AGENT = "data-pipe/1.0 (+ugaflix-url-resolver)"

# Safety limits so we don't hang forever when guessing CDN URLs
MAX_CANDIDATES = 450  # hard cap on candidate URLs per request
PER_REQUEST_TIMEOUT_CAP = 5  # max seconds per HTTP request, regardless of user timeout

# Month abbreviations and recent years for pearlpix path guessing
PEARLPIX_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
PEARLPIX_MONTH_LOWER = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
PEARLPIX_YEARS = ("2026", "2025", "2024")
PEARLPIX_WEEKS = ("1", "2", "3", "4", "5")
# VJ names seen in paths (e.g. JAN SINGLE/MARK/, MOVIES/VJ EMMY/)
VJ_PATH_NAMES = ("MARK", "EMMY", "HAM", "JUNIOR", "ICE P", "NELLY", "BANKS", "IVO", "JR")


@dataclass
class ParsedMovie:
    slug: str
    post_id: Optional[str]
    title_from_slug: str  # Title Case derived from slug
    title_from_page: Optional[str] = None  # Scraped from HTML if fetch_page=True
    raw_url: str = ""


def validate_detail_url(detail_url: str) -> None:
    parsed = urllib.parse.urlparse(detail_url)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != "ugaflix.com":
        raise ValueError("URL host must be ugaflix.com or www.ugaflix.com.")
    path = (parsed.path or "").strip("/")
    # Accept both /movies/details/<slug>/<id> and /movies/watch/<slug>/<id>
    if not (path.startswith("movies/details/") or path.startswith("movies/watch/")):
        raise ValueError("URL path must be like /movies/details/<slug>/<id> or /movies/watch/<slug>/<id>.")


def parse_detail_url(detail_url: str) -> ParsedMovie:
    validate_detail_url(detail_url)
    parsed = urllib.parse.urlparse(detail_url)
    path = (parsed.path or "").strip("/")
    # movies/details/the-night-agent-vj-ice-p/51  or  movies/watch/the-night-agent-vj-ice-p/51
    parts = path.split("/")
    if len(parts) < 4:
        raise ValueError("URL path must be /movies/details/<slug>/<id> or /movies/watch/<slug>/<id>.")
    # parts: ["movies", "details"|"watch", "slug", "id"]
    slug = parts[2]
    post_id = parts[3] if len(parts) > 3 and parts[3].isdigit() else None
    title_from_slug = slug_to_title(slug)
    return ParsedMovie(
        slug=slug,
        post_id=post_id,
        title_from_slug=title_from_slug,
        raw_url=detail_url,
    )


def slug_to_title(slug: str) -> str:
    """Convert URL slug to Title Case, stripping -vj-* suffix."""
    # Remove trailing -vj-xxx (e.g. the-night-agent-vj-ice-p -> the-night-agent)
    s = re.sub(r"-vj-[a-z0-9-]+$", "", slug, flags=re.IGNORECASE)
    # Replace hyphens with spaces and title-case
    words = s.replace("-", " ").split()
    return " ".join(w.capitalize() for w in words) if words else slug


def extract_vj_from_slug(slug: str) -> Optional[str]:
    """Extract VJ part from slug (e.g. firebreak-vj-ham -> 'vj ham', the-bluff-vj-emmy -> 'vj emmy')."""
    m = re.search(r"-vj-([a-z0-9-]+)$", slug, re.IGNORECASE)
    if not m:
        return None
    vj_part = m.group(1).replace("-", " ").strip()
    return f"vj {vj_part}" if vj_part else None


def extract_vj_upper_for_path(slug: str) -> Optional[str]:
    """VJ name in path style: 'MARK', 'VJ EMMY', 'ICE P' from slug (e.g. bazodee-vj-mark -> MARK)."""
    vj = extract_vj_from_slug(slug)
    if not vj:
        return None
    # "vj mark" -> "MARK", "vj ice p" -> "ICE P"
    rest = vj[3:].strip().upper()  # drop "vj "
    return rest if rest else None


def extract_vj_full_upper(slug: str) -> Optional[str]:
    """Full 'VJ EMMY' style for MOVIES/VJ EMMY/ paths."""
    vj = extract_vj_from_slug(slug)
    if not vj:
        return None
    return vj.upper()


def _looks_like_generic_page_title(title: Optional[str]) -> bool:
    """True if scraped title looks like a login/generic page, not a movie title (use slug for candidates)."""
    if not title or len(title) < 4:
        return True
    t = title.lower()
    if "login" in t and ("ugaflix" in t or "pearlpix" in t or "luganda" in t):
        return True
    if "forgot password" in t or "sign up" in t:
        return True
    return False


def effective_title_for_candidates(movie: ParsedMovie, use_scraped_title: bool) -> str:
    """Title to use for building CDN candidates; prefer slug when scraped title is a login/generic page."""
    raw = (movie.title_from_page if use_scraped_title and movie.title_from_page else None) or movie.title_from_slug
    if _looks_like_generic_page_title(raw):
        return movie.title_from_slug or raw
    return raw


def scrape_title_from_page(url: str, timeout: int) -> Optional[str]:
    """Fetch the detail page and extract the main title (e.g. 'Firebreak - Vj Ham')."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    # Prefer <h1> or og:title
    m = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", m.group(1).strip())
        # Strip " | Ugaflix ..." and similar
        if "|" in title:
            title = title.split("|")[0].strip()
        if title and len(title) < 200:
            return title
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()[:200]
    m = re.search(r'content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()[:200]
    return None


def title_to_folder_candidates(title: str, slug: str) -> List[str]:
    """Generate candidate folder names (lowercase, spaces)."""
    folders: Dict[str, None] = {}
    # From title: "The Night Agent" -> "the night agent", "night agent"
    t_lower = title.lower().strip()
    folders[t_lower] = None
    if t_lower.startswith("the "):
        folders[t_lower[4:].strip()] = None
    # From slug (no -vj-): "the-night-agent" -> "the night agent"
    slug_part = re.sub(r"-vj-[a-z0-9-]+$", "", slug, flags=re.IGNORECASE)
    slug_spaces = slug_part.replace("-", " ").lower()
    folders[slug_spaces] = None
    if slug_spaces.startswith("the "):
        folders[slug_spaces[4:].strip()] = None
    # Slug with dashes as folder (some CDNs use this)
    folders[slug_part] = None
    return list(folders.keys())


def title_to_filename_candidates(title: str, slug: str) -> List[str]:
    """Generate candidate filenames (dots, S01E01, etc.)."""
    names: Dict[str, None] = {}
    # Title with dots: "The Night Agent" -> "The.Night.Agent"
    dotted = ".".join(word.capitalize() for word in title.split())
    names[f"{dotted}.S01E01.mp4"] = None
    names[f"{dotted}.mp4"] = None
    names[f"{dotted}.mkv"] = None
    # Single word: "Firebreak" -> "Firebreak.mp4"
    if " " not in title:
        names[f"{title}.mp4"] = None
        names[f"{title}.S01E01.mp4"] = None
    # Slug-style filename
    slug_part = re.sub(r"-vj-[a-z0-9-]+$", "", slug, flags=re.IGNORECASE)
    dashed = slug_part.replace("-", "_")
    names[f"{dashed}.mp4"] = None
    names[f"{dashed}.mp4".replace("_", ".")] = None
    return list(names.keys())


def _pearlpix_candidates_for_base(
    movie: ParsedMovie,
    base: str,
    use_scraped_title: bool,
    title: str,
    title_hyphen: str,
    title_upper: str,
    title_cap: str,
    vj_raw: Optional[str],
    vj_lower: str,
    vj_upper: str,
    vj_cap: str,
    seen: Dict[str, None],
) -> List[str]:
    """Generate candidate URLs for one pearlpix base (jimmy or jim)."""
    out: List[str] = []

    def add(path: str) -> None:
        url = base + urllib.parse.quote(path, safe="/")
        if url not in seen:
            seen[url] = None
            out.append(url)

    vj_full = extract_vj_full_upper(movie.slug)

    # --- Highest priority: MONTH YEAR/WEEK N/TITLE VJ NAME YEAR.mp4 (e.g. FEB 2026/WEEK 4/IN COLD LIGHT VJ HAM 2026.mp4) ---
    if vj_full:
        for year in PEARLPIX_YEARS:
            for month in PEARLPIX_MONTHS:
                for week in PEARLPIX_WEEKS:
                    folder = f"{month} {year}/WEEK {week}"
                    add(f"{folder}/{title_upper} {vj_full} {year}.mp4")

    # --- JAN SINGLE/MARK/Title by Vj Mark_mp4.mp4 ---
    vj_path = extract_vj_upper_for_path(movie.slug)
    if vj_path:
        for month in PEARLPIX_MONTHS:
            folder = f"{month} SINGLE/{vj_path}"
            fname = f"{title} by {vj_cap}_mp4.mp4"
            add(f"{folder}/{fname}")
            add(f"{folder}/{title_cap} by {vj_cap}_mp4.mp4")
            add(f"{folder}/{title}.mp4")

    # --- MOVIES/VJ EMMY/10 aug/TITLE VJ EMMY 2025.mkv ---
    if vj_full:
        for month_abbr in PEARLPIX_MONTH_LOWER:
            for day in ("10", "01", "15", "20"):
                folder = f"MOVIES/{vj_full}/{day} {month_abbr}"
                for year in PEARLPIX_YEARS:
                    add(f"{folder}/{title_upper} {vj_full} {year}.mkv")
                    add(f"{folder}/{title_upper} {vj_full} {year}.mp4")

    # --- FEB 2026/WEEK 4: Title-VJ NELLY.mp4 and TITLE EMMY.2026.mp4 ---
    for year in PEARLPIX_YEARS:
        for month in PEARLPIX_MONTHS:
            for week in PEARLPIX_WEEKS:
                folder = f"{month} {year}/WEEK {week}"
                if vj_lower:
                    add(f"{folder}/{title_hyphen}-{vj_upper}.mp4")
                    add(f"{folder}/{title_hyphen}-VJ {vj_upper.replace('VJ ', '')}.mp4")
                vj_short = (vj_upper or "").replace("VJ ", "").strip()
                if vj_short:
                    add(f"{folder}/{title_upper} {vj_short}.{year}.mp4")

    # --- FEB 2026/WEEK N: Title__vj name 2026.mp4, Title (2).mp4 ---
    for year in PEARLPIX_YEARS:
        for month in PEARLPIX_MONTHS:
            for week in PEARLPIX_WEEKS:
                folder = f"{month} {year}/WEEK {week}"
                if vj_lower:
                    for name_title in (title_cap, title_hyphen):
                        add(f"{folder}/{name_title}__{vj_lower} {year}.mp4")
                    vj_short = (vj_upper or "").replace("VJ ", "").strip()
                    if vj_short:
                        add(f"{folder}/{title_upper} {vj_short}.{year}.mp4")
                for fname in (f"{title_cap} (2).mp4", f"{title_cap}.mp4"):
                    add(f"{folder}/{fname}")

    # Root-level Title__vj name YEAR.mp4
    if vj_lower:
        for year in PEARLPIX_YEARS:
            for name_title in (title_cap, title_hyphen):
                add(f"{name_title}__{vj_lower} {year}.mp4")

    # SERIES N / SHOW NAME / Title (2).mp4
    for series in ("SERIES 9", "SERIES 8", "SERIES 10"):
        for show in ("LOVE AND CROWN", "LOVE AND SWORD", ""):
            if show:
                add(f"{series}/{show}/{title_cap} (2).mp4")
                add(f"{series}/{show}/{title_cap}.mp4")
            else:
                add(f"{series}/{title_cap} (2).mp4")
                add(f"{series}/{title_cap}.mp4")

    return out


def build_pearlpix_candidates(movie: ParsedMovie, use_scraped_title: bool = True) -> List[str]:
    """Build candidate URLs for jimmy.pearlpix.xyz and jim.pearlpix.xyz."""
    title = effective_title_for_candidates(movie, use_scraped_title)
    if " - " in title and re.search(r"\s-\s+Vj\s+", title, re.IGNORECASE):
        title = re.sub(r"\s-\s+Vj\s+[^\s]+(?:\s|$).*", "", title).strip() or title
    vj_raw = extract_vj_from_slug(movie.slug)
    vj_lower = vj_raw.lower() if vj_raw else ""
    vj_upper = (vj_raw or "").upper()
    vj_cap = (vj_raw or "").title()
    title_hyphen = title.replace(" ", "-")
    title_upper = title.upper()
    title_cap = title

    candidates: List[str] = []
    seen: Dict[str, None] = {}

    for base in (PEARLPIX_JIMMY_BASE, PEARLPIX_JIM_BASE):
        chunk = _pearlpix_candidates_for_base(
            movie, base, use_scraped_title,
            title, title_hyphen, title_upper, title_cap,
            vj_raw, vj_lower, vj_upper, vj_cap, seen,
        )
        candidates.extend(chunk)
        if len(candidates) >= MAX_CANDIDATES:
            return candidates[:MAX_CANDIDATES]

    return candidates


def build_namz_root_candidates(movie: ParsedMovie, use_scraped_title: bool = True) -> List[str]:
    """Candidates for namzentertainments.xyz root: NEW PRO/Title.Year.720 VJ X@katflix..mp4."""
    title = effective_title_for_candidates(movie, use_scraped_title)
    if " - " in title and re.search(r"\s-\s+Vj\s+", title, re.IGNORECASE):
        title = re.sub(r"\s-\s+Vj\s+[^\s]+(?:\s|$).*", "", title).strip() or title
    vj_full = extract_vj_full_upper(movie.slug)  # "VJ JUNIOR"
    title_upper = title.upper()
    dotted = ".".join(word.capitalize() for word in title.split())
    candidates: List[str] = []
    seen: Dict[str, None] = {}

    project_folders = ("project 2", "project2")
    other_folders = ("NEW PRO", "Pro", "PRO")

    for year in PEARLPIX_YEARS:
        # Always prioritize project folders (no-year file first, then year-based variants)
        for folder in project_folders:
            # project 2/A BEAUTIFUL MIND VJ ULIO.mp4 (no year)
            if vj_full:
                # Some files use a double-space between title and VJ name, e.g. "NO MERCY  VJ ULIO"
                base_proj_single = f"{title_upper} {vj_full}.mp4"
                base_proj_double = f"{title_upper}  {vj_full}.mp4"
                for fname_proj in (base_proj_single, base_proj_double):
                    path_proj = f"{folder.rstrip('/')}/{fname_proj}" if folder else fname_proj
                    url_proj = NAMZENT_ROOT + urllib.parse.quote(path_proj, safe="/")
                    if url_proj not in seen:
                        seen[url_proj] = None
                        candidates.append(url_proj)
            # project 2/A.Beautiful.Mind.2026.720 VJ X@katflix..mp4
            if vj_full:
                fname = f"{dotted}.{year}.720 {vj_full}@katflix..mp4"
                path = f"{folder.rstrip('/')}/{fname}" if folder else fname
                url = NAMZENT_ROOT + urllib.parse.quote(path, safe="/")
                if url not in seen:
                    seen[url] = None
                    candidates.append(url)
            # project 2/A.Beautiful.Mind.2026.720p.mp4
            fname_plain = f"{dotted}.{year}.720p.mp4"
            path_plain = f"{folder.rstrip('/')}/{fname_plain}" if folder else fname_plain
            url_plain = NAMZENT_ROOT + urllib.parse.quote(path_plain, safe="/")
            if url_plain not in seen:
                seen[url_plain] = None
                candidates.append(url_plain)

        # Then NEW PRO / Pro / PRO year-based files
        for folder in other_folders:
            # NEW PRO/Title.Year.720 VJ X@katflix..mp4
            if vj_full:
                fname = f"{dotted}.{year}.720 {vj_full}@katflix..mp4"
                path = f"{folder.rstrip('/')}/{fname}" if folder else fname
                url = NAMZENT_ROOT + urllib.parse.quote(path, safe="/")
                if url not in seen:
                    seen[url] = None
                    candidates.append(url)
            # NEW PRO/Title.Year.720p.mp4
            fname_plain = f"{dotted}.{year}.720p.mp4"
            path_plain = f"{folder.rstrip('/')}/{fname_plain}" if folder else fname_plain
            url_plain = NAMZENT_ROOT + urllib.parse.quote(path_plain, safe="/")
            if url_plain not in seen:
                seen[url_plain] = None
                candidates.append(url_plain)
    return candidates


def build_candidates(movie: ParsedMovie, use_scraped_title: bool = True) -> List[str]:
    """Build ranked list of full candidate URLs: namz first (root + relord2), then pearlpix (jimmy+jim)."""
    title = effective_title_for_candidates(movie, use_scraped_title)
    if " - " in title and re.search(r"\s-\s+Vj\s+", title, re.IGNORECASE):
        title = re.sub(r"\s-\s+Vj\s+[^\s]+(?:\s|$).*", "", title).strip() or title
    candidates: List[str] = []
    seen: Dict[str, None] = {}

    # 1) namzentertainments.xyz root first (project 2/A BEAUTIFUL MIND VJ ULIO.mp4, NEW PRO/, etc.)
    for u in build_namz_root_candidates(movie, use_scraped_title):
        if u not in seen:
            seen[u] = None
            candidates.append(u)
            if len(candidates) >= MAX_CANDIDATES:
                return candidates

    # 2) namzentertainments.xyz relord2 (folder/filename)
    folders = title_to_folder_candidates(title, movie.slug)
    filenames = title_to_filename_candidates(title, movie.slug)
    for folder in folders:
        for name in filenames:
            path = f"{folder}/{name}"
            path_encoded = urllib.parse.quote(path, safe="/")
            url = f"{NAMZENT_BASE}{path_encoded}"
            if url not in seen:
                seen[url] = None
                candidates.append(url)
                if len(candidates) >= MAX_CANDIDATES:
                    return candidates

    # 3) jimmy + jim pearlpix (FEB 2026/WEEK 4, JAN SINGLE, MOVIES, etc.)
    for u in build_pearlpix_candidates(movie, use_scraped_title):
        if u not in seen:
            seen[u] = None
            candidates.append(u)
            if len(candidates) >= MAX_CANDIDATES:
                return candidates
    return candidates


def fetch_pearlpix_s3_keys(prefix: str, timeout: int, max_keys: int = 80) -> List[str]:
    """Try S3-style list on jimmy.pearlpix.xyz; return list of full URLs for keys matching prefix."""
    url = PEARLPIX_JIMMY_BASE + "?list-type=2&max-keys=" + str(max_keys) + "&prefix=" + urllib.parse.quote(prefix, safe="")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=min(timeout, PER_REQUEST_TIMEOUT_CAP)) as resp:
            xml = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    keys = re.findall(r"<Key>([^<]+)</Key>", xml)
    out = []
    for k in keys:
        k = k.strip()
        if k and (k.endswith(".mp4") or k.endswith(".mkv")):
            out.append(PEARLPIX_JIMMY_BASE + urllib.parse.quote(k, safe="/"))
    return out


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


def resolve_from_parsed_movie(
    movie: ParsedMovie,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Dict[str, object]:
    """Take a ParsedMovie, build candidates, validate, and return result dict. Shared by Ugaflix and Pearlpix."""
    start_time = time.monotonic()
    overall_deadline = start_time + max(timeout, DEFAULT_TIMEOUT)
    per_request_timeout = min(timeout, PER_REQUEST_TIMEOUT_CAP)

    # Build main candidates first (structured patterns: JAN SINGLE, MOVIES, WEEK, etc.)
    candidate_urls = []
    seen_main: Dict[str, None] = {}
    for u in build_candidates(movie, use_scraped_title=True):
        if u not in seen_main:
            seen_main[u] = None
            candidate_urls.append(u)
            if len(candidate_urls) >= MAX_CANDIDATES:
                break

    # Optional: append S3-style listing from jimmy.pearlpix.xyz (fallback; try a few prefixes)
    title = (effective_title_for_candidates(movie, True) or "").strip()
    if " - " in title and re.search(r"\s-\s+Vj\s+", title, re.IGNORECASE):
        title = re.sub(r"\s-\s+Vj\s+[^\s]+(?:\s|$).*", "", title).strip() or title
    s3_prefixes = []
    for month in PEARLPIX_MONTHS[:3]:
        for year in PEARLPIX_YEARS[:1]:
            s3_prefixes.append(f"{month} {year}")
    if title:
        first_word = title.split()[0].upper() if title.split() else ""
        if first_word and len(first_word) > 2:
            s3_prefixes.append(first_word)
    for prefix in s3_prefixes[:4]:
        for u in fetch_pearlpix_s3_keys(prefix, per_request_timeout, max_keys=50):
            if u not in seen_main:
                seen_main[u] = None
                candidate_urls.append(u)
                if len(candidate_urls) >= MAX_CANDIDATES:
                    break
        if len(candidate_urls) >= MAX_CANDIDATES:
            break

    candidate_urls = candidate_urls[:MAX_CANDIDATES]
    if movie.title_from_page and len(candidate_urls) < MAX_CANDIDATES:
        extra = build_candidates(
            ParsedMovie(
                slug=movie.slug,
                post_id=movie.post_id,
                title_from_slug=movie.title_from_slug,
                title_from_page=None,
                raw_url=movie.raw_url,
            ),
            use_scraped_title=False,
        )
        seen = {c: None for c in candidate_urls}
        for u in extra:
            if u not in seen:
                seen[u] = None
                candidate_urls.append(u)
                if len(candidate_urls) >= MAX_CANDIDATES:
                    break
    candidate_urls = candidate_urls[:MAX_CANDIDATES]

    checked: List[Dict[str, object]] = []
    max_attempts = max(1, retries + 1)

    for url in candidate_urls:
        # Stop if we've spent too long overall
        if time.monotonic() > overall_deadline:
            break
        attempts = 0
        status_code: Optional[int] = None
        methods_used: List[str] = []
        while attempts < max_attempts and status_code != 200:
            attempts += 1
            status_code = get_status_code(url, timeout=per_request_timeout, method="HEAD")
            methods_used.append("HEAD")
            if status_code != 200:
                status_code = get_status_code(url, timeout=per_request_timeout, method="GET")
                methods_used.append("GET")
            if status_code == 200:
                break
            if attempts < max_attempts:
                time.sleep(0.5)

        accepted = status_code == 200
        checked.append(
            {
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
                "parsed": {
                    "slug": movie.slug,
                    "post_id": movie.post_id,
                    "title_from_slug": movie.title_from_slug,
                    "title_from_page": movie.title_from_page,
                },
                "checks": checked,
            }

    return {
        "ok": False,
        "download_url": None,
        "status_code": None,
        "parsed": {
            "slug": movie.slug,
            "post_id": movie.post_id,
            "title_from_slug": movie.title_from_slug,
            "title_from_page": movie.title_from_page,
        },
        "checks": checked,
    }


def resolve_download_url(
    detail_url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    fetch_page: bool = True,
) -> Dict[str, object]:
    movie = parse_detail_url(detail_url)
    if fetch_page and movie.raw_url:
        movie.title_from_page = scrape_title_from_page(movie.raw_url, timeout)
    return resolve_from_parsed_movie(movie, timeout=timeout, retries=retries)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve Ugaflix movie detail URL to validated direct download URL (namzentertainments.xyz)."
    )
    parser.add_argument("url", help="Ugaflix detail URL (e.g. https://ugaflix.com/movies/details/...).")
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
