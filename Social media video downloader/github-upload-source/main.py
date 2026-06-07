from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, UnsupportedError


app = FastAPI(title="Storage-less Social Video Pipeline")


def cors_origins() -> list[str]:
    raw_origins = os.getenv("ALLOWED_ORIGINS", "*").strip()
    if raw_origins == "*":
        return ["*"]

    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "public"
CHUNK_SIZE = 1024 * 1024

YDL_OPTIONS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": True,
    "cachedir": False,
    "socket_timeout": 20,
    "retries": 2,
    "format": (
        "best[protocol^=http][ext=mp4][vcodec!=none][acodec!=none]/"
        "best[ext=mp4][vcodec!=none][acodec!=none]/"
        "best[protocol^=http][vcodec!=none][acodec!=none]/"
        "best[protocol^=http][vcodec!=none]/best"
    ),
}

UPSTREAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "video/webm,video/mp4,video/*,*/*",
    "Referer": "https://www.google.com/",
}


class ExtractPayload(BaseModel):
    url: str


def validate_http_url(raw_url: str) -> str:
    url = raw_url.strip()
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Please provide a valid http or https URL.")

    return url


def clean_error_message(error: Exception) -> str:
    message = str(error).replace("\n", " ").strip()
    message = re.sub(r"\s+", " ", message)
    return message.removeprefix("ERROR: ").strip()


def safe_filename(title: str | None) -> str:
    fallback = "video"
    if not title:
        return f"{fallback}.mp4"

    name = re.sub(r"[^A-Za-z0-9._ -]+", "", title)
    name = re.sub(r"\s+", " ", name).strip(" ._-")

    if not name:
        name = fallback

    if not name.lower().endswith(".mp4"):
        name = f"{name}.mp4"

    return name


def is_direct_http_video(format_info: dict[str, Any]) -> bool:
    url = format_info.get("url")
    protocol = (format_info.get("protocol") or "").lower()

    if not url or not str(url).startswith(("http://", "https://")):
        return False

    if "m3u8" in protocol or "dash" in protocol or format_info.get("fragments"):
        return False

    if format_info.get("vcodec") == "none":
        return False

    return True


def format_score(format_info: dict[str, Any]) -> float:
    protocol = (format_info.get("protocol") or "").lower()
    ext = (format_info.get("ext") or "").lower()
    acodec = format_info.get("acodec")

    score = 0.0
    if protocol in {"https", "http"}:
        score += 500
    if ext == "mp4":
        score += 400
    if acodec and acodec != "none":
        score += 300

    score += float(format_info.get("height") or 0)
    score += float(format_info.get("tbr") or 0) / 10
    return score


def pick_video_url(info: dict[str, Any]) -> str:
    selected_url = info.get("url")
    selected_protocol = (info.get("protocol") or "").lower()

    if (
        isinstance(selected_url, str)
        and selected_url.startswith(("http://", "https://"))
        and "m3u8" not in selected_protocol
        and "dash" not in selected_protocol
    ):
        return selected_url

    formats = [fmt for fmt in info.get("formats", []) if is_direct_http_video(fmt)]
    formats_with_audio = [fmt for fmt in formats if fmt.get("acodec") != "none"]
    candidates = formats_with_audio or formats

    if not candidates:
        raise HTTPException(
            status_code=422,
            detail=(
                "No directly streamable HTTP video format was found. "
                "This post may only expose segmented streams or require authentication."
            ),
        )

    return max(candidates, key=format_score)["url"]


def extract_video(url: str) -> dict[str, Any]:
    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise HTTPException(status_code=422, detail="No video metadata was returned.")

    if info.get("_type") == "playlist":
        raise HTTPException(status_code=400, detail="Playlist URLs are not supported. Please paste a single video URL.")

    source_url = pick_video_url(info)
    title = info.get("title") or "Untitled video"
    filename = safe_filename(title)
    encoded_source = quote(source_url, safe="")
    encoded_filename = quote(filename, safe="")

    return {
        "title": title,
        "thumbnail": info.get("thumbnail"),
        "source_url": source_url,
        "stream_url": f"/proxy-stream?url={encoded_source}&disposition=inline&filename={encoded_filename}",
        "download_url": f"/proxy-stream?url={encoded_source}&disposition=attachment&filename={encoded_filename}",
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/config.js", include_in_schema=False)
async def frontend_config() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "config.js", media_type="application/javascript")


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/extract")
async def extract(payload: ExtractPayload) -> dict[str, Any]:
    url = validate_http_url(payload.url)

    try:
        return await asyncio.to_thread(extract_video, url)
    except HTTPException:
        raise
    except UnsupportedError as error:
        raise HTTPException(status_code=400, detail=f"Unsupported URL: {clean_error_message(error)}") from error
    except DownloadError as error:
        message = clean_error_message(error)
        lower_message = message.lower()

        if "unsupported url" in lower_message:
            status_code = 400
        elif "429" in lower_message or "too many requests" in lower_message or "rate-limit" in lower_message:
            status_code = 429
        elif "login" in lower_message or "private" in lower_message or "cookies" in lower_message:
            status_code = 403
        else:
            status_code = 422

        raise HTTPException(status_code=status_code, detail=message) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {clean_error_message(error)}") from error


@app.get("/proxy-stream")
async def proxy_stream(
    request: Request,
    url: str = Query(..., description="Direct video CDN URL returned by /extract"),
    disposition: Literal["inline", "attachment"] = Query("inline"),
    filename: str = Query("video.mp4"),
) -> StreamingResponse:
    source_url = validate_http_url(url)
    range_header = request.headers.get("Range")
    request_headers = dict(UPSTREAM_HEADERS)

    if range_header:
        request_headers["Range"] = range_header

    client = httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=20, read=None, write=20, pool=20),
    )

    try:
        upstream_request = client.build_request("GET", source_url, headers=request_headers)
        upstream_response = await client.send(upstream_request, stream=True)
        upstream_response.raise_for_status()
    except httpx.HTTPStatusError as error:
        await client.aclose()
        status_code = error.response.status_code
        detail = "The remote CDN refused the stream request."

        if status_code == 403:
            detail = "The direct video link has expired or requires additional authorization."
        elif status_code == 404:
            detail = "The direct video link is no longer available."
        elif status_code == 429:
            detail = "The remote CDN is rate-limiting this request."

        raise HTTPException(status_code=status_code, detail=detail) from error
    except httpx.HTTPError as error:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Could not reach the remote video CDN: {error}") from error

    async def stream_bytes():
        try:
            async for chunk in upstream_response.aiter_bytes(CHUNK_SIZE):
                if chunk:
                    yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    response_headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'{disposition}; filename="{safe_filename(filename)}"',
    }

    for header in ("content-type", "content-length", "content-range", "accept-ranges"):
        value = upstream_response.headers.get(header)
        if value:
            response_headers[header] = value

    if "accept-ranges" not in response_headers:
        response_headers["accept-ranges"] = "bytes"

    return StreamingResponse(
        stream_bytes(),
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "video/mp4"),
        headers=response_headers,
    )
