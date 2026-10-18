"""Turning uploaded files, pasted text and fetched pages into searchable chunks.

Uploads are parsed in memory and never written to disk under a user-supplied
name, so path traversal through file names is not possible.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import ClassVar

from .text import normalize, separate_headings, split_sentences

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".html", ".htm"}
MAX_PDF_PAGES = 300
CHUNK_WORDS = 110


class IngestError(ValueError):
    """A user-facing problem with a source (shown verbatim in the UI)."""


@dataclass
class Section:
    text: str
    page: int | None = None


@dataclass
class ParsedSource:
    title: str
    kind: str  # pdf | text | markdown | html | url
    sections: list[Section] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.sections)

    @property
    def page_count(self) -> int | None:
        pages = {s.page for s in self.sections if s.page is not None}
        return len(pages) or None


@dataclass
class Chunk:
    ord: int
    page: int | None
    text: str


def safe_title(name: str, fallback: str = "Untitled source", *, is_path: bool = True) -> str:
    """Reduce a client-supplied file name (or free-text title) to a harmless display title."""
    if is_path:
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f<>]", "", name).strip(" .")
    return name[:120] or fallback


def extension_of(filename: str) -> str:
    match = re.search(r"(\.[A-Za-z0-9]{1,10})$", filename)
    return match.group(1).lower() if match else ""


class _HTMLText(HTMLParser):
    """Extracts readable text from HTML, skipping scripts, styles and navigation chrome."""

    SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "form", "iframe"}
    BLOCK: ClassVar[set[str]] = {
        "p",
        "div",
        "section",
        "article",
        "li",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tr",
        "blockquote",
        "pre",
        "td",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    return normalize(parser.title), normalize(" ".join(parser.parts))


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise IngestError("This file looks binary, not text. Upload PDF, TXT, Markdown or HTML.")
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", lambda m: m.group(0).strip("`"), text, flags=re.DOTALL)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"(\*\*|__|\*|_|`)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    return text


def _pdf_sections(data: bytes) -> list[Section]:
    if not data.startswith(b"%PDF-"):
        raise IngestError("This file has a .pdf name but is not a PDF document.")
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - dependency is pinned in requirements.txt
        raise IngestError("PDF support is not installed (missing pypdf).") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise IngestError("This PDF is password-protected. Remove the password and upload it again.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise IngestError(f"This PDF has more than {MAX_PDF_PAGES} pages. Split it into smaller files.")
        sections = []
        for number, page in enumerate(reader.pages, start=1):
            text = normalize(page.extract_text() or "")
            if text:
                sections.append(Section(text=text, page=number))
    except IngestError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise IngestError("This PDF could not be read. It may be damaged.") from exc
    if not sections:
        raise IngestError("No selectable text was found in this PDF. Scanned PDFs need OCR first.")
    return sections


def parse_upload(filename: str, data: bytes, max_chars: int) -> ParsedSource:
    """Parse an uploaded file into sections, enforcing type and size rules."""
    title = safe_title(filename)
    ext = extension_of(title)
    if ext not in ALLOWED_EXTENSIONS:
        raise IngestError("Unsupported file type. Upload PDF, TXT, Markdown (.md) or HTML files.")
    if not data:
        raise IngestError("The file is empty.")
    if ext == ".pdf":
        parsed = ParsedSource(title=title, kind="pdf", sections=_pdf_sections(data))
    elif ext in {".html", ".htm"}:
        page_title, body = html_to_text(_decode_text(data))
        parsed = ParsedSource(title=page_title[:120] or title, kind="html", sections=[Section(body)])
    elif ext in {".md", ".markdown"}:
        raw = _decode_text(data)
        heading = re.search(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", raw, flags=re.MULTILINE)
        if heading:
            title = safe_title(_strip_markdown(heading.group(1)), fallback=title)
        parsed = ParsedSource(title=title, kind="markdown", sections=[Section(normalize(separate_headings(_strip_markdown(raw))))])
    else:
        parsed = ParsedSource(title=title, kind="text", sections=[Section(normalize(separate_headings(_decode_text(data))))])
    return _finalise(parsed, max_chars)


def parse_pasted(title: str, text: str, max_chars: int) -> ParsedSource:
    clean_title = safe_title(title, fallback="Pasted notes", is_path=False)
    parsed = ParsedSource(title=clean_title, kind="text", sections=[Section(normalize(separate_headings(text)))])
    return _finalise(parsed, max_chars)


def _finalise(parsed: ParsedSource, max_chars: int) -> ParsedSource:
    parsed.sections = [s for s in parsed.sections if s.text.strip()]
    if parsed.char_count < 20:
        raise IngestError("This source contains too little text to research (under 20 characters).")
    if parsed.char_count > max_chars:
        raise IngestError(f"This source is too long ({parsed.char_count:,} characters; the limit is {max_chars:,}).")
    return parsed


def chunk_source(parsed: ParsedSource, chunk_words: int = CHUNK_WORDS) -> list[Chunk]:
    """Group sentences into ~chunk_words chunks without crossing page boundaries.

    Consecutive chunks share one sentence of overlap so evidence at a boundary is not lost.
    """
    chunks: list[Chunk] = []
    for section in parsed.sections:
        sentences = split_sentences(section.text)
        current: list[str] = []
        count = 0
        for sentence in sentences:
            n = len(sentence.split())
            if current and count + n > chunk_words:
                chunks.append(Chunk(len(chunks), section.page, "\n\n".join(current)))
                current = [current[-1]] if len(current) > 1 else []
                count = sum(len(s.split()) for s in current)
            current.append(sentence)
            count += n
        if current:
            chunks.append(Chunk(len(chunks), section.page, "\n\n".join(current)))
    return chunks
