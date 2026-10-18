import unittest

from sourcebound.agent import run_research
from sourcebound.report import to_markdown
from sourcebound.retrieval import Passage

from .helpers import sample_passages


class MarkdownExportTests(unittest.TestCase):
    def test_contains_sections_conflicts_and_sources(self):
        report = run_research("How much will the Riverside Library renovation cost and how long will it take?", sample_passages())
        md = to_markdown(report)
        self.assertTrue(md.startswith("# How much will the Riverside Library renovation cost"))
        self.assertIn("## Conflicting figures", md)
        self.assertIn("## Sources cited", md)
        self.assertIn("**Mode:** Extractive (quoted from sources)", md)
        self.assertIn("1. **Validate request**", md)

    def test_source_text_cannot_inject_markdown_or_html(self):
        text = "The <script>alert(1)</script> budget is [click](http://evil.example) $5 million for the bridge."
        report = run_research("What is the budget for the bridge?", [Passage("x:0", "x", "Doc", None, text)])
        md = to_markdown(report)
        self.assertNotIn("<script>", md)
        self.assertNotIn("[click](", md)  # the link syntax is escaped, so no link renders
        self.assertIn("\\[click\\](http://evil.example)", md)
        self.assertIn("\\<script\\>", md)


if __name__ == "__main__":
    unittest.main()
