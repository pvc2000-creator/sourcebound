"""Capture README screenshots from a real running Sourcebound server.

Starts the app on a throwaway database, drives it with Playwright (Chromium) through the
sample-library workflow, and writes PNGs to ./screenshots.

    python scripts/capture_screenshots.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screenshots"
QUESTION = "How much will the Riverside Library renovation cost and how long will it take?"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"server did not start at {url}")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    port = free_port()
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ | {
            "SOURCEBOUND_DB_PATH": str(Path(tmp) / "shots.db"),
            "SOURCEBOUND_PORT": str(port),
            "SOURCEBOUND_LLM_PROVIDER": "none",
        }
        server = subprocess.Popen(
            [sys.executable, "-m", "sourcebound"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            base = f"http://127.0.0.1:{port}"
            wait_for(f"{base}/api/health")
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(viewport={"width": 1360, "height": 860}, device_scale_factor=1, color_scheme="light")
                page = context.new_page()
                page.goto(base)
                page.wait_for_selector("#source-empty:not([hidden])")
                page.click("#load-sample")
                page.wait_for_selector(".chip")
                page.screenshot(path=str(OUT / "home.png"))

                page.fill("#question", QUESTION)
                page.click("#run-btn")
                page.wait_for_selector("#report:not([hidden])", timeout=20000)
                page.locator("#verdict").scroll_into_view_if_needed()
                page.evaluate("window.scrollTo(0, document.querySelector('#run').offsetTop - 16)")
                page.screenshot(path=str(OUT / "results.png"))

                page.locator(".conflict .cite").first.click()
                page.wait_for_selector("#drawer:not([hidden])")
                page.evaluate("window.scrollTo(0, document.querySelector('#conflicts').offsetTop - 16)")
                page.screenshot(path=str(OUT / "conflicts.png"))
                page.click("#close-drawer")

                page.click(".trace summary")
                page.evaluate("window.scrollTo(0, document.querySelector('.trace').offsetTop - 16)")
                page.screenshot(path=str(OUT / "workflow.png"))

                mobile = context.new_page()
                mobile.set_viewport_size({"width": 390, "height": 844})
                mobile.goto(page.url)
                mobile.wait_for_selector("#report:not([hidden])")
                mobile.evaluate("window.scrollTo(0, document.querySelector('#run').offsetTop - 8)")
                mobile.screenshot(path=str(OUT / "mobile.png"))
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print("Saved:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
