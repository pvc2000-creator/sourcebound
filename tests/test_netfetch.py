import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from sourcebound.netfetch import FetchError, fetch_source, is_public_ip, safe_fetch, validate_url


class ValidationTests(unittest.TestCase):
    def test_rejects_unsafe_urls(self):
        for url in [
            "ftp://example.com/file",
            "file:///etc/passwd",
            "http://user:pass@example.com/",
            "http://localhost/admin",
            "http://printer.local/",
            "http://intranet/",
            "https://example.com:8443/",
            "javascript:alert(1)",
            "http://[::1",
        ]:
            with self.subTest(url=url), self.assertRaises(FetchError):
                validate_url(url)

    def test_accepts_public_url(self):
        self.assertEqual(validate_url("https://example.org/a?b=1"), ("https", "example.org", 443, "/a?b=1"))

    def test_ip_classification(self):
        for ip in [
            "127.0.0.1",
            "10.0.0.5",
            "192.168.1.1",
            "169.254.169.254",
            "::1",
            "fc00::1",
            "::ffff:127.0.0.1",
            "0.0.0.0",
            "100.64.0.1",
        ]:
            with self.subTest(ip=ip):
                self.assertFalse(is_public_ip(ip))
        self.assertTrue(is_public_ip("93.184.216.34"))

    def test_private_resolution_is_blocked_before_connecting(self):
        with self.assertRaisesRegex(FetchError, "private"):
            safe_fetch("https://evil.example.com/", resolver=lambda host, port: ["169.254.169.254"])

    def test_mixed_resolution_is_blocked(self):
        with self.assertRaisesRegex(FetchError, "private"):
            safe_fetch("https://evil.example.com/", resolver=lambda host, port: ["93.184.216.34", "10.0.0.1"])


class Handler(BaseHTTPRequestHandler):
    routes: ClassVar[dict] = {}

    def do_GET(self):
        status, headers, body = self.routes.get(self.path, (404, {"Content-Type": "text/plain"}, b"missing"))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LocalFetchTests(unittest.TestCase):
    """Exercise the real HTTP path against a local server, with the private-IP guard relaxed for 127.0.0.1 only."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        html = b"<html><head><title>Bridge news</title></head><body><nav>menu</nav><p>The new bridge opened on Tuesday after three years of work.</p></body></html>"
        Handler.routes = {
            "/page": (200, {"Content-Type": "text/html; charset=utf-8"}, html),
            "/redirect-ok": (302, {"Location": "/page"}, b""),
            "/redirect-private": (302, {"Location": "http://10.0.0.7/secret"}, b""),
            "/big": (200, {"Content-Type": "text/plain"}, b"x" * 5000),
            "/image": (200, {"Content-Type": "image/png"}, b"\x89PNG"),
            "/private/doc": (200, {"Content-Type": "text/plain"}, b"Robots asked us not to read this page at all."),
            "/robots.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /private/\n"),
            "/forbidden": (403, {"Content-Type": "text/plain"}, b"no"),
        }

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def kwargs(self, **extra):
        return {"resolver": lambda host, port: [host], "ip_allowed": lambda ip: ip == "127.0.0.1", "allowed_ports": (self.port,), **extra}

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_fetches_and_parses_html(self):
        parsed, final = fetch_source(self.url("/redirect-ok"), 10_000, **self.kwargs())
        self.assertEqual(parsed.title, "Bridge news")
        self.assertEqual(parsed.kind, "url")
        self.assertEqual(final, self.url("/page"))
        self.assertNotIn("menu", parsed.sections[0].text)

    def test_redirect_to_private_address_is_blocked(self):
        with self.assertRaises(FetchError):
            safe_fetch(self.url("/redirect-private"), **self.kwargs())

    def test_size_limit(self):
        with self.assertRaisesRegex(FetchError, "too large"):
            safe_fetch(self.url("/big"), max_bytes=1000, **self.kwargs())

    def test_content_type_allowlist(self):
        with self.assertRaisesRegex(FetchError, "Unsupported content type"):
            safe_fetch(self.url("/image"), **self.kwargs())

    def test_robots_txt_is_respected(self):
        with self.assertRaisesRegex(FetchError, "robots.txt"):
            fetch_source(self.url("/private/doc"), 10_000, **self.kwargs())

    def test_http_errors_are_explained(self):
        with self.assertRaisesRegex(FetchError, "HTTP 403"):
            safe_fetch(self.url("/forbidden"), **self.kwargs())
        with self.assertRaisesRegex(FetchError, "404"):
            safe_fetch(self.url("/nope"), **self.kwargs())


if __name__ == "__main__":
    unittest.main()
