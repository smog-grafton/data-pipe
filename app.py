#!/usr/bin/env python3
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from mobifliks_url_resolver import resolve_download_url as mobifliks_resolve
from namz_url_resolver import resolve_download_url as namz_resolve
from pearlpix_url_resolver import resolve_download_url as pearlpix_resolve
from ugaflix_url_resolver import resolve_download_url as ugaflix_resolve


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("data-pipe-web")

app = FastAPI(
    title="Data Pipe – Download URL Resolvers",
    version="1.2.0",
    description="Resolve Mobifliks movies and TV shows, Ugaflix, Pearl Pix, or Namzentertainment URLs to validated direct download links.",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def detect_resolver(url: str) -> str:
    """Return 'mobifliks', 'ugaflix', 'pearlpix', or 'namz' based on URL host/path."""
    from urllib.parse import urlparse
    p = urlparse(url)
    host = (p.netloc or "").lower().replace("www.", "")
    path = (p.path or "").lower()
    query = p.query or ""
    if "namzentertainment" in host and ("prev" in path or "prev.php" in path) and "id=" in query:
        return "namz"
    if host == "pearlpix.net" and "/movies/details/" in path:
        return "pearlpix"
    # Treat any ugaflix.com movie URL (details or watch) as Ugaflix
    if "ugaflix.com" in host and "/movies/" in path:
        return "ugaflix"
    if "mobifliks.com" in host:
        return "mobifliks"
    return "mobifliks"  # default for backward compatibility


class ResolveRequest(BaseModel):
    url: str = Field(..., description="Mobifliks, Ugaflix, Pearl Pix, or Namz prev.php URL")
    timeout: int = Field(20, ge=5, le=60, description="HTTP timeout in seconds")
    retries: int = Field(1, ge=0, le=4, description="Retries per candidate")
    fetch_page: bool = Field(True, description="(Ugaflix only) Fetch detail page to scrape title")


class CheckResult(BaseModel):
    file: Optional[str] = None
    url: str
    status: Optional[int] = None
    attempts: int
    methods: List[str]
    accepted: bool


class EpisodeResult(BaseModel):
    episode_number: Optional[int] = None
    episode_title: Optional[str] = None
    eps_id: Optional[str] = None
    watch_url: Optional[str] = None
    download_url: Optional[str] = None
    status_code: Optional[int] = None
    ok: bool
    checks: List[CheckResult] = Field(default_factory=list)


class ResolveResponse(BaseModel):
    ok: bool
    download_url: Optional[str] = None
    status_code: Optional[int] = None
    resolver: str = Field(..., description="mobifliks, ugaflix, pearlpix, or namz")
    content_kind: str = Field("movie", description="movie, series, or episode")
    parsed: Dict[str, Any] = Field(..., description="Parsed movie metadata (shape depends on resolver)")
    checks: List[CheckResult] = Field(default_factory=list)
    episodes: List[EpisodeResult] = Field(default_factory=list)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": "Data Pipe – Movie & TV Download URL Resolver"},
    )


def _normalize_checks(checks: List[Dict[str, Any]]) -> List[CheckResult]:
    out = []
    for c in checks:
        out.append(CheckResult(
            file=c.get("file"),
            url=c["url"],
            status=c.get("status"),
            attempts=c.get("attempts", 0),
            methods=c.get("methods", []),
            accepted=c.get("accepted", False),
        ))
    return out


def _normalize_episodes(episodes: List[Dict[str, Any]]) -> List[EpisodeResult]:
    out = []
    for episode in episodes:
        out.append(EpisodeResult(
            episode_number=episode.get("episode_number"),
            episode_title=episode.get("episode_title"),
            eps_id=episode.get("eps_id"),
            watch_url=episode.get("watch_url"),
            download_url=episode.get("download_url"),
            status_code=episode.get("status_code"),
            ok=episode.get("ok", False),
            checks=_normalize_checks(episode.get("checks", [])),
        ))
    return out


@app.post("/api/resolve", response_model=ResolveResponse)
def resolve(payload: ResolveRequest) -> Any:
    resolver_name = detect_resolver(payload.url)
    try:
        if resolver_name == "ugaflix":
            result = ugaflix_resolve(
                detail_url=payload.url,
                timeout=payload.timeout,
                retries=payload.retries,
                fetch_page=payload.fetch_page,
            )
        elif resolver_name == "pearlpix":
            result = pearlpix_resolve(
                detail_url=payload.url,
                timeout=payload.timeout,
                retries=payload.retries,
                fetch_page=payload.fetch_page,
            )
        elif resolver_name == "namz":
            result = namz_resolve(
                prev_url=payload.url,
                timeout=payload.timeout,
                retries=payload.retries,
            )
        else:
            result = mobifliks_resolve(
                detail_url=payload.url,
                timeout=payload.timeout,
                retries=payload.retries,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected resolver failure")
        raise HTTPException(status_code=500, detail="Unexpected internal error") from exc

    result["resolver"] = resolver_name
    result["content_kind"] = result.get("content_kind", "movie")
    result["checks"] = _normalize_checks(result.get("checks", []))
    result["episodes"] = _normalize_episodes(result.get("episodes", []))
    logger.info(
        "resolve_request resolver=%s kind=%s ok=%s status=%s input=%s",
        resolver_name,
        result.get("content_kind"),
        result.get("ok"),
        result.get("status_code"),
        payload.url,
    )
    return result
