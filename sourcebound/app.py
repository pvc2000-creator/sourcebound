"""HTTP API and static front end (Starlette)."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .agent import AgentInputError, AgentLimits, run_research, validate_question
from .config import Settings, load_settings
from .ingest import IngestError, chunk_source, parse_pasted, parse_upload
from .llm import ChatModel, build_model
from .netfetch import fetch_source
from .report import to_markdown
from .store import Store

log = logging.getLogger("sourcebound")

PACKAGE_DIR = Path(__file__).parent
STATIC_DIR = PACKAGE_DIR / "static"
SAMPLE_DIR = PACKAGE_DIR / "sample_data"
WORKSPACE_RE = re.compile(r"^[a-f0-9]{32}$")
ID_RE = re.compile(r"^[a-f0-9]{32}$")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

SAMPLE_TITLES = {
    "alderbrook-gazette-article.txt": "Alderbrook Gazette: Residents back library renovation",
    "resident-comments.txt": "Public consultation: resident comments (excerpt)",
}

SAMPLE_QUESTIONS = [
    "How much will the Riverside Library renovation cost and how long will it take?",
    "How is the renovation being funded?",
    "What are the main risks of the renovation?",
]


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class SecurityHeadersMiddleware:
    """Adds security headers and rejects oversized request bodies early."""

    def __init__(self, app: ASGIApp, max_body: int) -> None:
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_body):
            response = JSONResponse({"error": "The request is too large."}, status_code=413)
            await response(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body:
                    raise ApiError(413, "The request is too large.")
            return message

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                for key, value in SECURITY_HEADERS.items():
                    raw.append((key.lower().encode(), value.encode()))
                message["headers"] = raw
            await send(message)

        await self.app(scope, limited_receive, send_with_headers)


class RateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    MAX_KEYS = 10_000

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._events) > self.MAX_KEYS:
                # Forget idle keys so rotating workspace ids cannot grow memory without bound.
                for stale in [k for k, q in self._events.items() if not q or now - q[-1] > window]:
                    del self._events[stale]
            q = self._events[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


def create_app(settings: Settings | None = None, store: Store | None = None, model: ChatModel | None = None) -> Starlette:
    settings = settings or load_settings()
    store = store or Store(settings.db_path)
    model = model if model is not None else build_model(settings.llm)
    executor = ThreadPoolExecutor(max_workers=settings.max_concurrent_runs, thread_name_prefix="research")
    limiter = RateLimiter()
    pending = {"count": 0}
    pending_lock = threading.Lock()
    max_upload = settings.max_upload_mb * 1024 * 1024

    def workspace_of(request: Request) -> str:
        ws = request.headers.get("x-workspace-id", "")
        if not WORKSPACE_RE.match(ws):
            raise ApiError(400, "Missing or invalid workspace id. Reload the page.")
        if settings.retention_hours:
            store.purge_older_than(settings.retention_hours)
        return ws

    def checked_id(value: str) -> str:
        if not ID_RE.match(value):
            raise ApiError(404, "Not found.")
        return value

    async def json_body(request: Request) -> dict[str, Any]:
        if "application/json" not in request.headers.get("content-type", ""):
            raise ApiError(415, "Send JSON with Content-Type: application/json.")
        try:
            data = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "The request body is not valid JSON.") from None
        if not isinstance(data, dict):
            raise ApiError(400, "The request body must be a JSON object.")
        return data

    def ensure_capacity(ws: str) -> None:
        if store.count_sources(ws) >= settings.max_sources_per_workspace:
            raise ApiError(409, f"You can keep up to {settings.max_sources_per_workspace} sources. Delete some before adding more.")

    def save(ws: str, parsed: Any, origin: str, is_sample: bool = False) -> dict[str, Any]:
        chunks = chunk_source(parsed)
        if not chunks:
            raise ApiError(422, "No readable sentences were found in this source.")
        return store.add_source(ws, parsed, chunks, origin, is_sample)

    # -- routes ------------------------------------------------------------

    async def index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True, "version": __version__})

    async def config(request: Request) -> Response:
        view = settings.public_view() | {"llm_available": model is not None, "llm_model": model.name if model else None}
        return JSONResponse(view | {"version": __version__, "sample_questions": SAMPLE_QUESTIONS})

    async def list_sources(request: Request) -> Response:
        ws = workspace_of(request)
        return JSONResponse({"sources": store.list_sources(ws)})

    async def upload_source(request: Request) -> Response:
        ws = workspace_of(request)
        ensure_capacity(ws)
        if not limiter.allow(f"add:{ws}", 30):
            raise ApiError(429, "Too many uploads in a minute. Wait a moment and try again.")
        form = await request.form(max_files=1, max_fields=4, max_part_size=64 * 1024)
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise ApiError(400, "Choose a file to upload.")
        data = await upload.read(max_upload + 1)
        await form.close()
        if len(data) > max_upload:
            raise ApiError(413, f"Files must be under {settings.max_upload_mb} MB.")
        parsed = parse_upload(upload.filename or "upload.txt", data, settings.max_source_chars)
        return JSONResponse({"source": save(ws, parsed, parsed.title)}, status_code=201)

    async def paste_source(request: Request) -> Response:
        ws = workspace_of(request)
        ensure_capacity(ws)
        data = await json_body(request)
        title, text = data.get("title", ""), data.get("text", "")
        if not isinstance(title, str) or not isinstance(text, str):
            raise ApiError(400, "Title and text must be strings.")
        parsed = parse_pasted(title or "Pasted notes", text, settings.max_source_chars)
        return JSONResponse({"source": save(ws, parsed, "pasted text")}, status_code=201)

    async def url_source(request: Request) -> Response:
        ws = workspace_of(request)
        if not settings.allow_url_fetch:
            raise ApiError(403, "Adding web pages is turned off on this server.")
        ensure_capacity(ws)
        if not limiter.allow(f"url:{ws}", 10):
            raise ApiError(429, "Too many links added in a minute. Wait a moment and try again.")
        data = await json_body(request)
        url = data.get("url", "")
        if not isinstance(url, str) or not url.strip():
            raise ApiError(400, "Enter a link starting with https://")
        parsed, final_url = await _in_thread(fetch_source, url.strip(), settings.max_source_chars)
        return JSONResponse({"source": save(ws, parsed, final_url)}, status_code=201)

    async def sample_sources(request: Request) -> Response:
        ws = workspace_of(request)
        if store.has_sample_sources(ws):
            raise ApiError(409, "The sample sources are already in your library.")
        files = sorted(SAMPLE_DIR.iterdir())
        if store.count_sources(ws) + len(files) > settings.max_sources_per_workspace:
            raise ApiError(409, "Not enough room for the sample sources. Delete some sources first.")
        added = []
        for path in files:
            parsed = parse_upload(path.name, path.read_bytes(), settings.max_source_chars)
            parsed.title = SAMPLE_TITLES.get(path.name, parsed.title)
            added.append(save(ws, parsed, f"sample: {path.name}", is_sample=True))
        return JSONResponse({"sources": added}, status_code=201)

    async def delete_source(request: Request) -> Response:
        ws = workspace_of(request)
        if not store.delete_source(ws, checked_id(request.path_params["source_id"])):
            raise ApiError(404, "That source no longer exists.")
        return Response(status_code=204)

    def execute(run_id: str, ws: str, question: str, source_ids: list[str] | None, use_llm: bool) -> None:
        try:
            store.set_running(run_id)
            passages = store.passages(ws, source_ids)
            limits = AgentLimits(
                max_seconds=settings.max_run_seconds,
                max_tool_calls=settings.max_tool_calls,
                max_sub_questions=settings.max_sub_questions,
                max_retries=settings.max_retries_per_sub_question,
                max_llm_calls=settings.llm.max_calls_per_run,
            )
            report = run_research(
                question, passages, limits=limits, model=model, use_llm=use_llm, emit=lambda e: store.append_event(run_id, e)
            )
            store.finish_run(run_id, report)
        except AgentInputError as exc:
            store.fail_run(run_id, str(exc))
        except Exception:
            log.exception("research run %s crashed", run_id)
            store.fail_run(run_id, "The research run hit an internal error. Please try again.")
        finally:
            with pending_lock:
                pending["count"] -= 1

    async def create_run(request: Request) -> Response:
        ws = workspace_of(request)
        data = await json_body(request)
        question = validate_question(str(data.get("question", "")))
        use_llm = bool(data.get("use_llm", False))
        if use_llm and model is None:
            raise ApiError(400, "No AI model is configured on this server. Turn off AI writing or configure a provider.")
        raw_ids = data.get("source_ids")
        source_ids: list[str] | None = None
        if raw_ids is not None:
            if not isinstance(raw_ids, list) or not all(isinstance(i, str) and ID_RE.match(i) for i in raw_ids) or len(raw_ids) > 500:
                raise ApiError(400, "source_ids must be a list of source ids.")
            source_ids = raw_ids
        if not store.passages(ws, source_ids):
            raise ApiError(400, "Add or select at least one source before researching.")
        if store.active_runs(ws) > 0:
            raise ApiError(429, "A research run is already in progress. Wait for it to finish.")
        if not limiter.allow(f"run:{ws}", settings.runs_per_minute):
            raise ApiError(429, "Too many research runs in a minute. Wait a moment and try again.")
        with pending_lock:
            if pending["count"] >= settings.max_concurrent_runs * 3:
                raise ApiError(503, "The server is busy. Try again in a minute.")
            pending["count"] += 1
        run_id = store.create_run(ws, question, "llm" if use_llm else "extractive")
        executor.submit(execute, run_id, ws, question, source_ids, use_llm)
        return JSONResponse({"id": run_id}, status_code=202)

    async def list_runs(request: Request) -> Response:
        return JSONResponse({"runs": store.list_runs(workspace_of(request))})

    async def get_run(request: Request) -> Response:
        run = store.get_run(workspace_of(request), checked_id(request.path_params["run_id"]))
        if run is None:
            raise ApiError(404, "That research run no longer exists.")
        return JSONResponse(run)

    async def export_run(request: Request) -> Response:
        run = store.get_run(workspace_of(request), checked_id(request.path_params["run_id"]))
        if run is None or not run["report"]:
            raise ApiError(404, "That report is not available.")
        fmt = request.path_params["fmt"]
        if fmt == "md":
            return PlainTextResponse(
                to_markdown(run["report"]),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="sourcebound-report.md"'},
            )
        if fmt == "json":
            payload = {k: v for k, v in run["report"].items()} | {"id": run["id"], "created_at": run["created_at"]}
            return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="sourcebound-report.json"'})
        raise ApiError(404, "Unknown export format.")

    async def delete_run(request: Request) -> Response:
        if not store.delete_run(workspace_of(request), checked_id(request.path_params["run_id"])):
            raise ApiError(404, "That run does not exist or is still in progress.")
        return Response(status_code=204)

    async def delete_workspace(request: Request) -> Response:
        store.delete_workspace(workspace_of(request))
        return Response(status_code=204)

    async def api_error(request: Request, exc: Exception) -> Response:
        if isinstance(exc, ApiError):
            return JSONResponse({"error": exc.message}, status_code=exc.status)
        if isinstance(exc, (IngestError, AgentInputError)):
            return JSONResponse({"error": str(exc)}, status_code=422)
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"error": "Something went wrong on the server."}, status_code=500)

    async def not_found(request: Request, exc: Exception) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Not found."}, status_code=404)
        return PlainTextResponse("Not found", status_code=404)

    routes = [
        Route("/", index),
        Route("/api/health", health),
        Route("/api/config", config),
        Route("/api/sources", list_sources),
        Route("/api/sources/upload", upload_source, methods=["POST"]),
        Route("/api/sources/text", paste_source, methods=["POST"]),
        Route("/api/sources/url", url_source, methods=["POST"]),
        Route("/api/sources/sample", sample_sources, methods=["POST"]),
        Route("/api/sources/{source_id}", delete_source, methods=["DELETE"]),
        Route("/api/runs", list_runs, methods=["GET"]),
        Route("/api/runs", create_run, methods=["POST"]),
        Route("/api/runs/{run_id}", get_run, methods=["GET"]),
        Route("/api/runs/{run_id}", delete_run, methods=["DELETE"]),
        Route("/api/runs/{run_id}/export.{fmt}", export_run),
        Route("/api/workspace", delete_workspace, methods=["DELETE"]),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    app = Starlette(
        routes=routes,
        middleware=[Middleware(SecurityHeadersMiddleware, max_body=max_upload + 1024 * 1024)],
        exception_handlers={ApiError: api_error, IngestError: api_error, AgentInputError: api_error, 404: not_found, Exception: api_error},
    )
    app.state.store = store
    app.state.executor = executor
    app.state.settings = settings
    return app


async def _in_thread(func: Any, *args: Any) -> Any:
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(func, *args)
