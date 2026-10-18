import unittest

from sourcebound.ingest import IngestError, chunk_source, html_to_text, parse_pasted, parse_upload, safe_title

from .helpers import make_pdf

LIMIT = 100_000


class TitleTests(unittest.TestCase):
    def test_path_components_are_removed(self):
        self.assertEqual(safe_title("../../etc/passwd.txt"), "passwd.txt")
        self.assertEqual(safe_title("C:\\Users\\me\\notes.md"), "notes.md")

    def test_control_and_markup_characters_are_removed(self):
        self.assertEqual(safe_title("re<script>port\x00.txt"), "rescriptport.txt")

    def test_empty_name_falls_back(self):
        self.assertEqual(safe_title("..."), "Untitled source")


class ParseTests(unittest.TestCase):
    def test_plain_text(self):
        src = parse_upload("notes.txt", b"The bridge opened in 1932. It cost a lot.", LIMIT)
        self.assertEqual(src.kind, "text")
        self.assertIn("bridge", src.sections[0].text)

    def test_markdown_title_and_formatting(self):
        src = parse_upload("x.md", b"# Bridge report\n\nThe **bridge** is [old](http://x).", LIMIT)
        self.assertEqual(src.title, "Bridge report")
        self.assertIn("The bridge is old.", src.sections[0].text)

    def test_html_skips_scripts_and_navigation(self):
        title, text = html_to_text("<html><title>T</title><nav>Menu</nav><script>alert(1)</script><p>Body text here.</p></html>")
        self.assertEqual(title, "T")
        self.assertEqual(text, "Body text here.")

    def test_pdf_pages_are_kept(self):
        pdf = make_pdf(["The first page talks about bridges.", "The second page talks about tunnels."])
        src = parse_upload("doc.pdf", pdf, LIMIT)
        self.assertEqual(src.kind, "pdf")
        self.assertEqual([s.page for s in src.sections], [1, 2])
        self.assertIn("tunnels", src.sections[1].text)

    def test_rejects_unsupported_extension(self):
        with self.assertRaisesRegex(IngestError, "Unsupported file type"):
            parse_upload("run.exe", b"MZ....", LIMIT)

    def test_rejects_fake_pdf(self):
        with self.assertRaisesRegex(IngestError, "not a PDF"):
            parse_upload("doc.pdf", b"hello world, not a pdf", LIMIT)

    def test_rejects_corrupt_pdf(self):
        with self.assertRaises(IngestError), self.assertLogs("pypdf", level="WARNING"):
            parse_upload("doc.pdf", b"%PDF-1.4\ngarbage", LIMIT)

    def test_rejects_binary_text(self):
        with self.assertRaisesRegex(IngestError, "binary"):
            parse_upload("a.txt", b"\x00\x01\x02" * 50, LIMIT)

    def test_rejects_empty_and_tiny(self):
        with self.assertRaises(IngestError):
            parse_upload("a.txt", b"", LIMIT)
        with self.assertRaisesRegex(IngestError, "too little text"):
            parse_pasted("t", "short", LIMIT)

    def test_rejects_too_long(self):
        with self.assertRaisesRegex(IngestError, "too long"):
            parse_pasted("t", "word " * 1000, 100)


class ChunkTests(unittest.TestCase):
    def test_chunks_respect_size_and_overlap(self):
        text = " ".join(f"Sentence number {i} is about topic {i}." for i in range(60))
        chunks = chunk_source(parse_pasted("t", text, LIMIT), chunk_words=40)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c.text.split()) <= 48 for c in chunks))
        last_of_first = chunks[0].text.split("\n\n")[-1]
        self.assertTrue(chunks[1].text.startswith(last_of_first))

    def test_chunks_do_not_cross_pages(self):
        src = parse_upload("d.pdf", make_pdf(["Page one sentence here.", "Page two sentence here."]), LIMIT)
        chunks = chunk_source(src)
        self.assertEqual([(c.page, c.text) for c in chunks], [(1, "Page one sentence here."), (2, "Page two sentence here.")])


if __name__ == "__main__":
    unittest.main()
