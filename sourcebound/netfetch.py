"""Fetching a public web page as a source, with server-side request forgery protection.

Defences:
* only http/https on ports 80/443, no credentials in the URL;
* the host name is resolved once and *every* address must be a public, global IP;
* the connection is pinned to that validated address (no second DNS lookup, so DNS
  rebinding cannot swap in a private address), while TLS still verifies the host name;
* redirects are followed manually (max 3) and each hop is re-validated;
* response size, time and content type are capped;
* robots.txt is honoured for the "SourceboundBot" user agent.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from .ingest import IngestError, ParsedSource, Section, _decode_text, _finalise, _pdf_sections, html_to_text, safe_title
from .text import normalize

USER_AGENT = "SourceboundBot/0.1 (+https://github.com/pvc2000-creator/sourcebound)"
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/pdf")
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp")

Resolver = Callable[[str, int], list[str]]


class FetchError(IngestError):
    """A user-facing reason why a URL could not be added."""


@dataclass
class FetchResult:
    url: str
    content_type: str
    body: bytes


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast and not addr.is_reserved


def default_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"Could not find the website {host!r}.") from exc
    return sorted({str(info[4][0]) for info in infos})


def validate_url(url: str, allowed_ports: tuple[int, ...] = (80, 443)) -> tuple[str, str, int, str]:
    """Return (scheme, host, port, path) or raise FetchError."""
    url = url.strip()
    if len(url) > 2000:
        raise FetchError("That URL is too long.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchError("That does not look like a valid URL.") from exc
    if parts.scheme not in {"http", "https"}:
        raise FetchError("Only http:// and https:// links can be added.")
    if parts.username or parts.password:
        raise FetchError("Links containing a username or password are not allowed.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise FetchError("That URL has no host name.")
    if host == "localhost" or host.endswith(BLOCKED_SUFFIXES) or ("." not in host and ":" not in host):
        raise FetchError("Links to local or internal network addresses are not allowed.")
    default_port = 443 if parts.scheme == "https" else 80
    port = port or default_port
    if port not in allowed_ports:
        raise FetchError("Only links on the standard web ports (80 and 443) are allowed.")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, host, port, path


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


def _open(scheme: str, host: str, ip: str, port: int, timeout: float) -> http.client.HTTPConnection:
    cls = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
    return cls(host, ip, port, timeout)


def safe_fetch(
    url: str,
    *,
    max_bytes: int = 3_000_000,
    timeout: float = 10.0,
    max_redirects: int = 3,
    resolver: Resolver = default_resolver,
    ip_allowed: Callable[[str], bool] = is_public_ip,
    accept_types: tuple[str, ...] = ALLOWED_TYPES,
    allowed_ports: tuple[int, ...] = (80, 443),
) -> FetchResult:
    deadline = time.monotonic() + timeout * 2
    current = url
    for _hop in range(max_redirects + 1):
        scheme, host, port, path = validate_url(current, allowed_ports)
        addresses = resolver(host, port)
        if not addresses or not all(ip_allowed(ip) for ip in addresses):
            raise FetchError("Links to private, local or reserved network addresses are not allowed.")
        conn = _open(scheme, host, addresses[0], port, timeout)
        try:
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": ", ".join(accept_types)})
            resp = conn.getresponse()
            if resp.status in {301, 302, 303, 307, 308}:
                location = resp.getheader("Location")
                if not location:
                    raise FetchError("The website sent a redirect without a destination.")
                current = urljoin(current, location)
                continue
            if resp.status == 404:
                raise FetchError("That page was not found (HTTP 404).")
            if resp.status in {401, 403}:
                raise FetchError(f"That page requires permission to view (HTTP {resp.status}). Sourcebound only reads public pages.")
            if resp.status == 429:
                raise FetchError("The website is rate-limiting requests (HTTP 429). Try again later.")
            if resp.status >= 400:
                raise FetchError(f"The website returned an error (HTTP {resp.status}).")
            content_type = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if not any(content_type == t for t in accept_types):
                raise FetchError(f"Unsupported content type {content_type or 'unknown'!r}. Links must point to a web page, text or PDF.")
            declared = resp.getheader("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise FetchError("That page is too large to add.")
            body = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise FetchError("The website took too long to respond.")
                block = resp.read(65536)
                if not block:
                    break
                body.extend(block)
                if len(body) > max_bytes:
                    raise FetchError("That page is too large to add.")
            return FetchResult(current, content_type, bytes(body))
        except (OSError, http.client.HTTPException) as exc:
            if isinstance(exc, FetchError):
                raise
            raise FetchError(f"Could not connect to {host}. Check the link or try again later.") from exc
        finally:
            conn.close()
    raise FetchError("Too many redirects.")


def robots_allows(url: str, **fetch_kwargs: object) -> bool:
    allowed_ports = fetch_kwargs.get("allowed_ports", (80, 443))
    scheme, host, port, _ = validate_url(url, allowed_ports)  # type: ignore[arg-type]
    robots_url = f"{scheme}://{host}{'' if port in {80, 443} else f':{port}'}/robots.txt"
    try:
        result = safe_fetch(robots_url, accept_types=("text/plain",), max_bytes=500_000, **fetch_kwargs)  # type: ignore[arg-type]
    except FetchError:
        return True  # no readable robots.txt means no restrictions were published
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(result.body.decode("utf-8", "replace").splitlines())
    return parser.can_fetch("SourceboundBot", url)


def fetch_source(url: str, max_chars: int, **fetch_kwargs: object) -> tuple[ParsedSource, str]:
    """Fetch a public URL and parse it. Returns (parsed source, final URL)."""
    if not robots_allows(url, **fetch_kwargs):
        raise FetchError("This website's robots.txt asks automated tools not to read that page, so it was not added.")
    result = safe_fetch(url, **fetch_kwargs)  # type: ignore[arg-type]
    host = urlsplit(result.url).hostname or "web page"
    if result.content_type == "application/pdf":
        parsed = ParsedSource(
            title=safe_title(result.url.rstrip("/").rsplit("/", 1)[-1] or host), kind="url", sections=_pdf_sections(result.body)
        )
    elif result.content_type in {"text/html", "application/xhtml+xml"}:
        title, text = html_to_text(_decode_text(result.body))
        parsed = ParsedSource(title=(title or host)[:120], kind="url", sections=[Section(text)])
    else:
        parsed = ParsedSource(title=host, kind="url", sections=[Section(normalize(_decode_text(result.body)))])
    return _finalise(parsed, max_chars), result.url
