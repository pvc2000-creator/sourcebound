"""Shared test utilities: sample passages, a fake chat model, tiny PDFs and a live server."""

from __future__ import annotations

import json
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import uvicorn

from sourcebound.config import Settings
from sourcebound.ingest import chunk_source, parse_upload
from sourcebound.retrieval import Passage

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "sourcebound" / "sample_data"
WORKSPACE = "0123456789abcdef0123456789abcdef"
OTHER_WORKSPACE = "fedcba9876543210fedcba9876543210"


def sample_passages() -> list[Passage]:
    passages: list[Passage] = []
    for i, path in enumerate(sorted(SAMPLE_DIR.iterdir())):
        parsed = parse_upload(path.name, path.read_bytes(), 400_000)
        for chunk in chunk_source(parsed):
            passages.append(Passage(f"s{i}:{chunk.ord}", f"s{i}", parsed.title, chunk.page, chunk.text))
    return passages


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal valid PDF with one line of Helvetica text per page."""
    objects: list[bytes] = []
    page_ids = [3 + 2 * i for i in range(len(pages))]
    font_id = 3 + 2 * len(pages)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for i, text in enumerate(pages):
        content_id = page_ids[i] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


class FakeModel:
    """A scripted ChatModel: returns queued responses (or raises queued exceptions)."""

    name = "fake-model"

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *, max_tokens: int = 900, json_mode: bool = False) -> str:
        self.prompts.append((system, user))
        if not self.responses:
            raise AssertionError("FakeModel ran out of scripted responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(system, user)
        return item if isinstance(item, str) else json.dumps(item)


EVIDENCE_LINE = re.compile(r"^\[(E\d+)\] \(source: [^)]*\) (.+)$", re.MULTILINE)
SUBQ_LINE = re.compile(r"^Sub-question (q\d+):", re.MULTILINE)


def writer_from_evidence(transform=lambda text: text):
    """A fake writer that restates the first evidence item under each sub-question."""

    def respond(system: str, user: str) -> dict:
        sections = []
        blocks = SUBQ_LINE.split(user)[1:]
        for sid, block in zip(blocks[0::2], blocks[1::2], strict=True):
            match = EVIDENCE_LINE.search(block)
            sentences = [{"text": transform(match.group(2)), "evidence": [match.group(1)]}] if match else []
            sections.append({"id": sid, "sentences": sentences})
        return {"sections": sections}

    return respond


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class LiveServer:
    """Runs the real ASGI app under uvicorn in a background thread."""

    def __init__(self, settings: Settings | None = None, model: Any = None) -> None:
        from sourcebound.app import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.settings = settings or Settings(db_path=Path(self.tmp.name) / "test.db", max_upload_mb=1, max_sources_per_workspace=8)
        self.app = create_app(self.settings, model=model)
        self.port = free_port()
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> LiveServer:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.app.state.executor.shutdown(wait=True)
        self.app.state.store.close()
        self.tmp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        workspace: str | None = WORKSPACE,
    ) -> tuple[int, dict[str, str], bytes]:
        hdrs = dict(headers or {})
        if workspace:
            hdrs["X-Workspace-Id"] = workspace
        body = data
        if json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, headers=hdrs, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=15) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, {k.lower(): v for k, v in err.headers.items()}, err.read()

    def json(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        status, _, body = self.request(method, path, **kwargs)
        return status, (json.loads(body) if body else None)

    def wait_for_run(self, run_id: str, workspace: str = WORKSPACE, timeout: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, run = self.json("GET", f"/api/runs/{run_id}", workspace=workspace)
            assert status == 200, run
            if run["status"] not in {"queued", "running"}:
                return run
            time.sleep(0.05)
        raise AssertionError("run did not finish in time")


def multipart(filename: str, content: bytes, field: str = "file") -> tuple[bytes, str]:
    boundary = "----sourceboundtestboundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"
