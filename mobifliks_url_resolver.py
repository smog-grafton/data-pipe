#!/usr/bin/env python3
"""
Resolve Mobifliks movie, series, or episode URLs into direct download URLs.

The script:
1) Detects movie detail, series detail, or episode watch URLs.
2) Generates ranked direct-download filename candidates.
3) Confirms candidates over HTTP and only accepts status 200.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


DIRECT_BASE = "https://mobifliks.info/downloadmp4.php?file="
SERIES_DIRECT_BASE = "https://mobifliks.info/downloadserie.php?file="
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 1
VALID_HOSTS = {"www.mobifliks.com", "mobifliks.com"}
DETAIL_PATH_PATTERN = re.compile(r"/downloadvideo(?:\d+)?\.php$")
SERIES_DETAIL_PATH_PATTERN = re.compile(r"/downloadseries[12]\.php$")
EPISODE_DETAIL_PATH_PATTERN = re.compile(r"/downloadepisode2\.php$")
EPISODE_LINK_PATTERN = re.compile(
    r'<h3>\s*<a href="(?P<href>https://www\.mobifliks\.com/downloadepisode2\.php\?[^"]+)"[^>]*>'
    r"\s*(?P<label>\d+\.\s*[^<]+?)\s*</a>\s*</h3>",
    flags=re.IGNORECASE,
)
FALLBACK_SERIES_VJ_NAMES = ["Ice P", "Junior", "Junior 1", "Ivo", "Nelly", "Ulio"]
USER_AGENT = "narabox-data-pipe/1.0 (+mobifliks-url-resolver)"


@dataclass
class ParsedMovie:
    title: str
    year: Optional[str]
    vj_name: Optional[str]
    language: Optional[str]
    raw_vid_name: str


@dataclass
class ParsedSeries:
    title: str
    year: Optional[str]
    vj_name: Optional[str]
    language: Optional[str]
    raw_series_name: str
    series_id: str


@dataclass
class ParsedEpisodeInput:
    eps_id: str
    series_id: str
    series_name: str
    watch_url: str


@dataclass
class SeriesEpisode:
    eps_id: str
    series_id: str
    series_name: str
    episode_number: int
    episode_title: str
    watch_url: str


@dataclass(frozen=True)
class SeriesPatternHint:
    name_variant: str
    vj_variant: str
    suffix: str


@dataclass(frozen=True)
class Candidate:
    file_path: str
    url: str
    hint: Optional[SeriesPatternHint] = None


def validate_host(detail_url: str) -> None:
    parsed = urllib.parse.urlparse(detail_url)
    host = (parsed.netloc or "").lower()
    if host not in VALID_HOSTS:
        raise ValueError("URL host must be mobifliks.com or www.mobifliks.com.")


def validate_detail_url(detail_url: str) -> None:
    validate_host(detail_url)
    parsed = urllib.parse.urlparse(detail_url)
    if not DETAIL_PATH_PATTERN.search(parsed.path):
        raise ValueError("URL path must end with /downloadvideo.php or /downloadvideo<number>.php.")


def validate_series_url(detail_url: str) -> None:
    validate_host(detail_url)
    parsed = urllib.parse.urlparse(detail_url)
    if not SERIES_DETAIL_PATH_PATTERN.search(parsed.path):
        raise ValueError("URL path must end with /downloadseries1.php or /downloadseries2.php.")


def validate_episode_url(detail_url: str) -> None:
    validate_host(detail_url)
    parsed = urllib.parse.urlparse(detail_url)
    if not EPISODE_DETAIL_PATH_PATTERN.search(parsed.path):
        raise ValueError("URL path must end with /downloadepisode2.php.")


def parse_name_metadata(decoded: str) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
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
            vj_name = strip_trailing_language(vj_match.group(1))

        lang_match = re.search(r"\b(Luganda|English|Swahili|Kiswahili)\b", inner, flags=re.IGNORECASE)
        if lang_match:
            language = normalize_language(lang_match.group(1))

    if not language and re.search(r"\bLuganda\b", decoded, flags=re.IGNORECASE):
        language = "luganda"

    return clean_token(title), year, vj_name, language


def parse_detail_url(detail_url: str) -> ParsedMovie:
    validate_detail_url(detail_url)
    parsed = urllib.parse.urlparse(detail_url)
    query = urllib.parse.parse_qs(parsed.query)
    vid_name = query.get("vid_name", [None])[0]
    if not vid_name:
        raise ValueError("Missing vid_name query parameter in the provided URL.")

    decoded = urllib.parse.unquote_plus(vid_name).strip()
    title, year, vj_name, language = parse_name_metadata(decoded)
    return ParsedMovie(
        title=title,
        year=year,
        vj_name=vj_name,
        language=language,
        raw_vid_name=decoded,
    )


def parse_series_url(detail_url: str) -> ParsedSeries:
    validate_series_url(detail_url)
    parsed = urllib.parse.urlparse(detail_url)
    query = urllib.parse.parse_qs(parsed.query)
    series_id = query.get("series_id", [None])[0]
    series_name = query.get("series_name", [None])[0]
    if not series_id:
        raise ValueError("Missing series_id query parameter in the provided URL.")
    if not series_name:
        raise ValueError("Missing series_name query parameter in the provided URL.")

    decoded = urllib.parse.unquote_plus(series_name).strip()
    title, year, vj_name, language = parse_name_metadata(decoded)
    return ParsedSeries(
        title=title,
        year=year,
        vj_name=vj_name,
        language=language,
        raw_series_name=decoded,
        series_id=series_id,
    )


def parse_episode_input(detail_url: str) -> ParsedEpisodeInput:
    validate_episode_url(detail_url)
    parsed = urllib.parse.urlparse(detail_url)
    query = urllib.parse.parse_qs(parsed.query)
    eps_id = query.get("eps_id", [None])[0]
    series_id = query.get("series_id", [None])[0]
    series_name = query.get("series_name", [None])[0]
    if not eps_id:
        raise ValueError("Missing eps_id query parameter in the provided URL.")
    if not series_id:
        raise ValueError("Missing series_id query parameter in the provided URL.")
    if not series_name:
        raise ValueError("Missing series_name query parameter in the provided URL.")

    return ParsedEpisodeInput(
        eps_id=eps_id,
        series_id=series_id,
        series_name=urllib.parse.unquote_plus(series_name).strip(),
        watch_url=normalize_url(detail_url),
    )


def split_title_and_parentheses(value: str) -> Tuple[str, Optional[str]]:
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
    return re.sub(r"\s+", " ", value).strip(" -")


def strip_trailing_language(value: str) -> str:
    return clean_token(re.sub(r"\b(Luganda|English|Swahili|Kiswahili)\b\s*$", "", value, flags=re.IGNORECASE))


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


def series_name_variants(title: str) -> List[str]:
    bases = [clean_token(title)]
    if title.lower().startswith("the "):
        stripped = clean_token(title[4:])
        if stripped:
            bases.append(stripped)

    variants: List[str] = []
    for base in bases:
        variants.append(base)
        variants.extend(title_variants(base))

        titled = clean_token(base.title())
        if titled:
            variants.append(titled)

    unique: Dict[str, None] = {}
    for item in variants:
        if item:
            unique[item] = None
    return list(unique.keys())


def vj_variants(vj_name: Optional[str]) -> List[str]:
    if not vj_name:
        return []
    cleaned = strip_trailing_language(vj_name)
    bases = [cleaned]
    if cleaned.lower() == "junior":
        bases.append("Junior 1")
    elif cleaned.lower() == "junior 1":
        bases.append("Junior")

    unique: Dict[str, None] = {}
    for base in bases:
        unique[f"Vj {base}"] = None
        unique[f"VJ {base}"] = None
    return list(unique.keys())


def series_vj_variants(vj_name: Optional[str]) -> List[str]:
    if vj_name:
        return vj_variants(vj_name)

    variants: List[str] = []
    for fallback_name in FALLBACK_SERIES_VJ_NAMES:
        variants.extend(vj_variants(fallback_name))

    unique: Dict[str, None] = {}
    for item in variants:
        unique[item] = None
    return list(unique.keys())


def filename_suffix_variants() -> List[str]:
    return [" - Mobifliks.com", "", "- Mobifliks.com"]


def build_candidates(movie: ParsedMovie) -> List[str]:
    folder = movie.language or "luganda"
    names: List[str] = []
    vj_names = vj_variants(movie.vj_name)
    titles = title_variants(movie.title)
    suffixes = filename_suffix_variants()

    for title in titles:
        for vj in vj_names:
            for suffix in suffixes:
                names.append(f"{title} by {vj}{suffix}.mp4")

    if movie.year:
        for title in titles:
            for vj in vj_names:
                for suffix in suffixes:
                    names.append(f"{title} ({movie.year}) by {vj}{suffix}.mp4")

    if vj_names and movie.year and movie.language:
        for suffix in suffixes:
            names.append(
                f"{titles[0]} ({movie.year} - {vj_names[0]} - {movie.language.capitalize()}){suffix}.mp4"
            )

    unique: Dict[str, None] = {}
    for item in names:
        unique[item] = None

    return [f"{folder}/{name}" for name in unique.keys()]


def build_series_candidates(
    series: ParsedSeries,
    episode: SeriesEpisode,
    preferred_hint: Optional[SeriesPatternHint] = None,
) -> List[Candidate]:
    name_variants = series_name_variants(series.title)
    vj_names = series_vj_variants(series.vj_name)
    suffixes = filename_suffix_variants()
    folder = series.language or "luganda"

    ordered_specs: List[Tuple[str, str, str]] = []
    if preferred_hint is not None:
        ordered_specs.append((preferred_hint.name_variant, preferred_hint.vj_variant, preferred_hint.suffix))

    for name_variant in name_variants:
        for vj_variant in vj_names:
            for suffix in suffixes:
                ordered_specs.append((name_variant, vj_variant, suffix))

    candidates: List[Candidate] = []
    seen: Dict[str, None] = {}
    for name_variant, vj_variant, suffix in ordered_specs:
        file_name = f"{name_variant} {episode.episode_number} by {vj_variant}{suffix}.mp4"
        file_path = f"{folder}/{name_variant}/{file_name}"
        if file_path in seen:
            continue
        seen[file_path] = None
        candidates.append(
            Candidate(
                file_path=file_path,
                url=as_series_direct_url(file_path, episode.eps_id),
                hint=SeriesPatternHint(name_variant=name_variant, vj_variant=vj_variant, suffix=suffix),
            )
        )

    return candidates


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    encoded_query = urllib.parse.urlencode(query_pairs, doseq=True, quote_via=urllib.parse.quote)
    return urllib.parse.urlunparse(parsed._replace(query=encoded_query))


def build_public_series_url(series_id: str, series_name: str) -> str:
    encoded_query = urllib.parse.urlencode(
        {"series_id": series_id, "series_name": series_name},
        quote_via=urllib.parse.quote,
    )
    return f"https://www.mobifliks.com/downloadseries1.php?{encoded_query}"


def as_public_series_url(detail_url: str) -> str:
    parsed = urllib.parse.urlparse(detail_url)
    query = urllib.parse.parse_qs(parsed.query)
    series_id = query.get("series_id", [None])[0]
    series_name = query.get("series_name", [None])[0]
    if not series_id or not series_name:
        raise ValueError("Missing series_id or series_name query parameter in the provided URL.")
    return build_public_series_url(series_id, urllib.parse.unquote_plus(series_name).strip())


def fetch_html(url: str, timeout: int) -> str:
    request = urllib.request.Request(url=url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return html.unescape(response.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Unable to fetch Mobifliks page (HTTP {exc.code}).") from exc
    except Exception as exc:
        raise ValueError("Unable to fetch Mobifliks page.") from exc


def parse_series_episodes_from_html(html_text: str) -> List[SeriesEpisode]:
    episodes: List[SeriesEpisode] = []
    seen_eps_ids: Dict[str, None] = {}

    for match in EPISODE_LINK_PATTERN.finditer(html_text):
        watch_url = normalize_url(match.group("href"))
        label = clean_token(match.group("label"))
        label_match = re.match(r"(?P<number>\d+)\.\s*(?P<title>.+)", label)
        if not label_match:
            continue

        parsed_watch = urllib.parse.urlparse(watch_url)
        query = urllib.parse.parse_qs(parsed_watch.query)
        eps_id = query.get("eps_id", [None])[0]
        series_id = query.get("series_id", [None])[0]
        series_name = query.get("series_name", [None])[0]
        if not eps_id or not series_id or not series_name or eps_id in seen_eps_ids:
            continue

        seen_eps_ids[eps_id] = None
        episodes.append(
            SeriesEpisode(
                eps_id=eps_id,
                series_id=series_id,
                series_name=urllib.parse.unquote_plus(series_name).strip(),
                episode_number=int(label_match.group("number")),
                episode_title=clean_token(label_match.group("title")),
                watch_url=watch_url,
            )
        )

    return episodes


def fetch_series_episodes(series_url: str, timeout: int) -> Tuple[str, List[SeriesEpisode]]:
    public_series_url = as_public_series_url(series_url)
    html_text = fetch_html(public_series_url, timeout=timeout)
    episodes = parse_series_episodes_from_html(html_text)
    return public_series_url, episodes


def as_direct_url(file_path: str) -> str:
    encoded = urllib.parse.quote(file_path, safe="/")
    return f"{DIRECT_BASE}{encoded}"


def as_series_direct_url(file_path: str, eps_id: str) -> str:
    encoded = urllib.parse.quote(file_path, safe="/")
    return f"{SERIES_DIRECT_BASE}{encoded}&eps_id={urllib.parse.quote(str(eps_id))}"


def get_status_code(url: str, timeout: int, method: str) -> Optional[int]:
    request = urllib.request.Request(
        url=url,
        method=method,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def validate_candidates(
    candidates: Sequence[Candidate],
    timeout: int,
    retries: int,
) -> Tuple[Optional[Candidate], Optional[int], List[Dict[str, object]]]:
    checked: List[Dict[str, object]] = []

    for candidate in candidates:
        attempts = 0
        status_code: Optional[int] = None
        methods_used: List[str] = []
        max_attempts = max(1, retries + 1)

        while attempts < max_attempts and status_code != 200:
            attempts += 1

            head_code = get_status_code(candidate.url, timeout=timeout, method="HEAD")
            methods_used.append("HEAD")
            status_code = head_code

            if status_code != 200:
                get_code = get_status_code(candidate.url, timeout=timeout, method="GET")
                methods_used.append("GET")
                status_code = get_code

            if status_code == 200:
                break

            if attempts < max_attempts:
                time.sleep(0.5)

        accepted = status_code == 200
        checked.append(
            {
                "file": candidate.file_path,
                "url": candidate.url,
                "status": status_code,
                "attempts": attempts,
                "methods": methods_used,
                "accepted": accepted,
            }
        )

        if accepted:
            return candidate, status_code, checked

    return None, None, checked


def resolve_movie_download_url(
    detail_url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Dict[str, object]:
    movie = parse_detail_url(detail_url)
    candidates = [Candidate(file_path=file_path, url=as_direct_url(file_path)) for file_path in build_candidates(movie)]
    accepted, status_code, checked = validate_candidates(candidates, timeout=timeout, retries=retries)

    return {
        "ok": accepted is not None,
        "download_url": accepted.url if accepted is not None else None,
        "status_code": status_code,
        "content_kind": "movie",
        "parsed": movie.__dict__,
        "checks": checked,
        "episodes": [],
    }


def resolve_series_episode(
    series: ParsedSeries,
    episode: SeriesEpisode,
    timeout: int,
    retries: int,
    preferred_hint: Optional[SeriesPatternHint] = None,
) -> Tuple[Dict[str, object], Optional[SeriesPatternHint]]:
    candidates = build_series_candidates(series, episode, preferred_hint=preferred_hint)
    accepted, status_code, checked = validate_candidates(candidates, timeout=timeout, retries=retries)

    result = {
        "episode_number": episode.episode_number,
        "episode_title": episode.episode_title,
        "eps_id": episode.eps_id,
        "watch_url": episode.watch_url,
        "download_url": accepted.url if accepted is not None else None,
        "ok": accepted is not None,
        "status_code": status_code,
        "checks": checked,
    }
    return result, accepted.hint if accepted is not None else preferred_hint


def resolve_series_download_url(
    detail_url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Dict[str, object]:
    series = parse_series_url(detail_url)
    public_series_url, episodes = fetch_series_episodes(detail_url, timeout=timeout)

    preferred_hint: Optional[SeriesPatternHint] = None
    resolved_episodes: List[Dict[str, object]] = []
    resolved_any = False

    for episode in episodes:
        episode_result, preferred_hint = resolve_series_episode(
            series,
            episode,
            timeout=timeout,
            retries=retries,
            preferred_hint=preferred_hint,
        )
        resolved_any = resolved_any or bool(episode_result["ok"])
        resolved_episodes.append(episode_result)

    parsed = series.__dict__.copy()
    parsed.update(
        {
            "series_url": normalize_url(detail_url),
            "public_series_url": public_series_url,
            "episode_count": len(episodes),
        }
    )

    return {
        "ok": resolved_any,
        "download_url": None,
        "status_code": None,
        "content_kind": "series",
        "parsed": parsed,
        "checks": [],
        "episodes": resolved_episodes,
    }


def resolve_episode_download_url(
    detail_url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Dict[str, object]:
    episode_input = parse_episode_input(detail_url)
    public_series_url = build_public_series_url(episode_input.series_id, episode_input.series_name)
    series = parse_series_url(public_series_url)
    _, episodes = fetch_series_episodes(public_series_url, timeout=timeout)

    matching_episode = next((episode for episode in episodes if episode.eps_id == episode_input.eps_id), None)
    if matching_episode is None:
        raise ValueError("Episode was not found on the public Mobifliks series page.")

    episode_result, _ = resolve_series_episode(
        series,
        matching_episode,
        timeout=timeout,
        retries=retries,
    )

    parsed = series.__dict__.copy()
    parsed.update(
        {
            "watch_url": episode_input.watch_url,
            "public_series_url": public_series_url,
            "episode_number": matching_episode.episode_number,
            "episode_title": matching_episode.episode_title,
            "eps_id": matching_episode.eps_id,
        }
    )

    return {
        "ok": episode_result["ok"],
        "download_url": episode_result["download_url"],
        "status_code": episode_result["status_code"],
        "content_kind": "episode",
        "parsed": parsed,
        "checks": episode_result["checks"],
        "episodes": [episode_result],
    }


def resolve_download_url(
    detail_url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Dict[str, object]:
    validate_host(detail_url)
    path = urllib.parse.urlparse(detail_url).path.lower()

    if DETAIL_PATH_PATTERN.search(path):
        return resolve_movie_download_url(detail_url, timeout=timeout, retries=retries)
    if SERIES_DETAIL_PATH_PATTERN.search(path):
        return resolve_series_download_url(detail_url, timeout=timeout, retries=retries)
    if EPISODE_DETAIL_PATH_PATTERN.search(path):
        return resolve_episode_download_url(detail_url, timeout=timeout, retries=retries)

    raise ValueError(
        "URL path must end with /downloadvideo.php, /downloadvideo<number>.php, "
        "/downloadseries1.php, /downloadseries2.php, or /downloadepisode2.php."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and validate Mobifliks movie or series download URLs."
    )
    parser.add_argument(
        "url",
        help=(
            "Mobifliks URL "
            "(downloadvideo.php?... / downloadvideo2.php?... / downloadseries1.php?... / "
            "downloadseries2.php?... / downloadepisode2.php?...)"
        ),
    )
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
        if result["content_kind"] == "series":
            episodes = result.get("episodes", [])
            resolved = [episode for episode in episodes if episode.get("ok")]
            print(f"Resolved {len(resolved)} of {len(episodes)} series episodes.")
            for episode in episodes:
                download_url = episode.get("download_url") or "NOT FOUND"
                print(f'Episode {episode.get("episode_number")}: {download_url}')
        elif result["ok"]:
            print("Resolved direct download URL:")
            print(result["download_url"])
            print("Validation status: 200 OK")
        else:
            print("No validated (200 OK) direct download URL found.")
            print("Try increasing timeout or inspecting candidate patterns with --json.")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
