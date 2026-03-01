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

from mobifliks_url_resolver import resolve_download_url


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("mobifliks-web")

app = FastAPI(
    title="Mobifliks Download URL Resolver",
    version="1.0.0",
    description="Resolve Mobifliks movie detail URLs to validated direct download links.",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class ResolveRequest(BaseModel):
    url: str = Field(..., description="Mobifliks detail page URL")
    timeout: int = Field(20, ge=5, le=60, description="HTTP timeout in seconds")
    retries: int = Field(1, ge=0, le=4, description="Retries per candidate")


class ParsedMovieResponse(BaseModel):
    title: str
    year: Optional[str] = None
    vj_name: Optional[str] = None
    language: Optional[str] = None
    raw_vid_name: str


class CheckResult(BaseModel):
    file: str
    url: str
    status: Optional[int] = None
    attempts: int
    methods: List[str]
    accepted: bool


class ResolveResponse(BaseModel):
    ok: bool
    download_url: Optional[str] = None
    status_code: Optional[int] = None
    parsed: ParsedMovieResponse
    checks: List[CheckResult]


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": "Mobifliks URL Resolver"},
    )


@app.post("/api/resolve", response_model=ResolveResponse)
def resolve(payload: ResolveRequest) -> Any:
    try:
        result = resolve_download_url(
            detail_url=payload.url,
            timeout=payload.timeout,
            retries=payload.retries,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected resolver failure")
        raise HTTPException(status_code=500, detail="Unexpected internal error") from exc

    logger.info(
        "resolve_request ok=%s status=%s input=%s",
        result.get("ok"),
        result.get("status_code"),
        payload.url,
    )
    return result
