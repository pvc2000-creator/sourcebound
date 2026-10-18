"""End-to-end browser tests (Playwright + Chromium) against a real running server.

Run with:  python tests/e2e/test_ui.py
Requires:  pip install -r requirements-dev.txt && python -m playwright install chromium
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

from tests.helpers import LiveServer

QUESTION = "How much will the Riverside Library renovation cost and how long will it take?"


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class BrowserWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = LiveServer().__enter__()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.live.__exit__(None, None, None)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        self.page.on("console", lambda msg: self.errors.append(msg.text) if msg.type == "error" else None)
        self.page.goto(self.live.base)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], "browser console errors")

    def test_sample_workflow_produces_cited_report(self):
        page = self.page
        expect(page.locator("#source-empty")).to_be_visible()
        page.click("#load-sample")
        expect(page.locator(".source-item")).to_have_count(4)
        expect(page.locator("#scope-note")).to_have_text("Searching all 4 sources.")
        page.locator(".chip").first.click()
        expect(page.locator("#question")).to_have_value(QUESTION)
        page.click("#run-btn")
        expect(page.locator("#verdict")).to_contain_text("Complete")
        expect(page.locator(".answer")).to_have_count(2)
        expect(page.locator(".conflict")).to_have_count(2)
        expect(page.locator("#history-list li")).to_have_count(1)

        # Citations open the evidence drawer with the quote highlighted, and Escape returns focus.
        chip = page.locator(".answer .cite").first
        chip.click()
        expect(page.locator("#drawer")).to_be_visible()
        expect(page.locator("#drawer-context mark")).to_have_count(1)
        page.keyboard.press("Escape")
        expect(page.locator("#drawer")).to_be_hidden()
        expect(chip).to_be_focused()

        # The report survives a reload via the URL hash.
        page.reload()
        expect(page.locator("#verdict")).to_contain_text("Complete")

        with page.expect_download() as download:
            page.click("#dl-md")
        self.assertEqual(download.value.suggested_filename, "sourcebound-report.md")

    def test_validation_and_empty_states(self):
        page = self.page
        page.click("#run-btn")
        expect(page.locator("#question-error")).to_contain_text("fuller question")
        page.fill("#question", "What does the budget say about parks?")
        page.click("#run-btn")
        expect(page.locator("#question-error")).to_contain_text("Add a source first")

    def test_deselecting_all_sources_blocks_research(self):
        page = self.page
        page.click("#load-sample")
        expect(page.locator(".source-item")).to_have_count(4)
        for box in page.locator(".source-item input[type=checkbox]").all():
            box.uncheck()
        expect(page.locator("#scope-note")).to_have_text("Searching 0 of 4 sources.")
        page.fill("#question", QUESTION)
        page.click("#run-btn")
        expect(page.locator("#question-error")).to_contain_text("Select at least one source")

    def test_pasted_markup_is_rendered_as_text(self):
        page = self.page
        page.click("#open-paste")
        page.fill("#paste-name", "<img src=x onerror=alert(1)>")
        page.fill("#paste-text", 'The harbour budget is $3 million. <img src=x onerror="document.title=1">')
        page.click("#paste-form button[type=submit]")
        expect(page.locator(".source-item")).to_have_count(1)
        expect(page.locator(".source-title")).to_have_text("img src=x onerror=alert(1)")
        page.fill("#question", "What is the harbour budget?")
        page.click("#run-btn")
        expect(page.locator("#verdict")).to_be_visible()
        self.assertEqual(page.locator("main img").count(), 0)
        self.assertNotEqual(page.title(), "1")

    def test_unanswerable_question_reports_failure(self):
        page = self.page
        page.click("#load-sample")
        expect(page.locator(".source-item")).to_have_count(4)
        page.fill("#question", "What is the population of Lima in Peru?")
        page.click("#run-btn")
        expect(page.locator("#verdict")).to_contain_text("No answer")
        expect(page.locator(".no-evidence")).to_have_count(1)

    def test_mobile_layout_has_no_horizontal_scroll(self):
        page = self.context.new_page()
        page.set_viewport_size({"width": 375, "height": 800})
        page.goto(self.live.base)
        page.click("#load-sample")
        page.fill("#question", QUESTION)
        page.click("#run-btn")
        expect(page.locator("#verdict")).to_be_visible()
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 0)


if __name__ == "__main__":
    unittest.main()
