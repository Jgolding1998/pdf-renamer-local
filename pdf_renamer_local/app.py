from __future__ import annotations

import asyncio
import hmac
import json
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import MB, Settings
from .extract import FIELDS
from .naming import valid_prefix
from .plan import build_plan, build_zip
from .uploads import UploadError, read_upload

STATIC_DIR = Path(__file__).parent / "static"
CSRF_COOKIE = "pdfr_csrf"
CSRF_HEADER = "x-csrf-token"
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
PLAN_ID_RE = re.compile(r"[0-9a-f]{64}")
SAFE_METHODS = {"GET", "HEAD"}

SECURITY_HEADERS = [
    (b"content-security-policy",
     b"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
     b"connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cache-control", b"no-store"),
]


def split_host(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else ""
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class GuardMiddleware:
    """Rejects unknown Host headers (DNS rebinding) and adds security headers."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.allowed = set(settings.allowed_hosts)
        self.jobs = asyncio.Semaphore(settings.max_parallel_jobs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        seen: set[bytes] = set()
        for name, _ in scope["headers"]:
            name = name.lower()
            if name == b"transfer-encoding" or (name in {b"host", b"origin", b"content-length", b"content-type"} and name in seen):
                response = error(400, "Ambiguous request headers.")
                await response(scope, receive, self._wrap(send))
                return
            seen.add(name)
        host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
        try:
            authority = urlsplit("//" + host)
            valid_host = (
                bool(host) and not re.search(r"[\s/@?#\\]", host)
                and authority.hostname in self.allowed
                and authority.username is None and authority.password is None
                and (authority.port is None or 1 <= authority.port <= 65535)
            )
        except ValueError:
            valid_host = False
        if not valid_host:
            response = error(400, "Host not allowed.")
            await response(scope, receive, self._wrap(send))
            return
        is_upload = scope["method"] == "POST" and scope["path"] in {"/api/preview", "/api/zip"}
        if is_upload:
            if self.jobs.locked():
                response = error(503, "All processing slots are busy. Try again shortly.")
                response.headers["Retry-After"] = "1"
                await response(scope, receive, self._wrap(send))
                return
            await self.jobs.acquire()
        try:
            await self.app(scope, receive, self._wrap(send))
        finally:
            if is_upload:
                self.jobs.release()

    @staticmethod
    def _wrap(send: Send) -> Send:
        async def wrapped(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = [h for h in message.get("headers", []) if h[0] != b"cache-control"]
                message["headers"] = headers + SECURITY_HEADERS
            await send(message)

        return wrapped


def check_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if not origin:
        return "Missing Origin header."
    try:
        parts = urlsplit(origin)
    except ValueError:
        return "Cross-origin request rejected."
    if (parts.scheme != request.url.scheme or parts.scheme not in ("http", "https")
            or parts.netloc.lower() != request.headers.get("host", "").lower()
            or parts.username is not None or parts.password is not None
            or parts.path or parts.query or parts.fragment or any(char.isspace() for char in origin)):
        return "Cross-origin request rejected."
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site != "same-origin":
        return "Cross-site request rejected."
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not TOKEN_RE.fullmatch(cookie) or not TOKEN_RE.fullmatch(header) or not hmac.compare_digest(cookie, header):
        return "Missing or invalid CSRF token."
    return None


def error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


def create_app(settings: Settings | None = None) -> ASGIApp:
    settings = settings or Settings.from_env()
    app = FastAPI(title="PDF Renamer Local", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    template = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
    limits = json.dumps(
        {
            "maxFiles": settings.max_files,
            "maxFileBytes": settings.max_file_bytes,
            "maxTotalBytes": settings.max_total_bytes,
            "maxPages": settings.max_pages,
        }
    )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        token = request.cookies.get(CSRF_COOKIE, "")
        if not TOKEN_RE.fullmatch(token):
            token = secrets.token_urlsafe(32)
        html = (
            template.replace("{{CSRF}}", token)
            .replace("{{LIMITS}}", limits.replace('"', "&quot;"))
            .replace("{{MAX_FILES}}", str(settings.max_files))
            .replace("{{MAX_FILE_MB}}", str(settings.max_file_bytes // MB))
            .replace("{{MAX_TOTAL_MB}}", str(settings.max_total_bytes // MB))
            .replace("{{MAX_PAGES}}", str(settings.max_pages))
        )
        response = HTMLResponse(html)
        response.set_cookie(
            CSRF_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    async def plan_request(request: Request, *, require_plan_id: bool = False):
        problem = check_origin(request)
        if problem:
            return None, None, error(403, problem)
        try:
            fields, files = await asyncio.wait_for(read_upload(request, settings), timeout=settings.upload_timeout)
        except TimeoutError:
            return None, None, error(408, "Upload took too long. Try a smaller batch.")
        except UploadError as exc:
            return None, None, error(exc.status, exc.message)
        if require_plan_id and not PLAN_ID_RE.fullmatch(fields.get("plan_id", "")):
            return None, None, error(422, "Invalid preview id. Preview the current files before downloading.")
        mode = fields.get("mode", "customer")
        prefix = fields.get("prefix", "")
        if mode not in FIELDS:
            return None, None, error(422, "Unknown rename mode.")
        if not valid_prefix(prefix):
            return None, None, error(422, "Prefix may only use letters, digits, - and _ (up to 32).")
        if not files:
            return None, None, error(422, "Choose at least one PDF.")
        plan = await run_in_threadpool(build_plan, files, mode, prefix, settings)
        return fields, (files, plan), None

    @app.post("/api/preview")
    async def preview(request: Request) -> Response:
        _, bundle, problem = await plan_request(request)
        if problem:
            return problem
        return JSONResponse(bundle[1])

    @app.post("/api/zip")
    async def download(request: Request) -> Response:
        fields, bundle, problem = await plan_request(request, require_plan_id=True)
        if problem:
            return problem
        files, plan = bundle
        if not hmac.compare_digest(fields.get("plan_id", ""), plan["plan_id"]):
            return error(409, "The files or options changed since the preview. Preview again.")
        if plan["summary"]["renamed"] == 0:
            return error(422, "No files could be renamed, so there is nothing to download.")
        body = await run_in_threadpool(build_zip, files, plan)
        return Response(
            body,
            media_type="application/zip",
            headers={
                "content-disposition": 'attachment; filename="renamed-pdfs.zip"',
                "x-plan-id": plan["plan_id"],
            },
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return GuardMiddleware(app, settings)
