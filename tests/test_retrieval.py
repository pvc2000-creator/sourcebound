import unittest

from sourcebound.retrieval import BM25Index, EvidenceSentence, Passage, score_sentences, select_evidence


def passage(i: int, source: str, text: str) -> Passage:
    return Passage(f"{source}:{i}", source, f"Source {source}", None, text)


PASSAGES = [
    passage(0, "a", "The tunnel will cost $2 billion to build. Construction begins next spring."),
    passage(1, "a", "Local bakeries sell bread and cakes. The bakery opens early every day."),
    passage(2, "b", "Engineers expect the tunnel project to take 48 months. Traffic will fall afterwards."),
    passage(3, "c", "The tunnel will cost $2 billion to build, officials repeated at the meeting today."),
]


class BM25Tests(unittest.TestCase):
    def setUp(self):
        self.index = BM25Index(PASSAGES)

    def test_relevant_passages_rank_first(self):
        results = self.index.search("tunnel cost")
        self.assertEqual({r.passage.chunk_id for r in results[:2]}, {"a:0", "c:3"})
        self.assertNotIn("a:1", [r.passage.chunk_id for r in results])

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.index.search("the of and"), [])

    def test_expansion_terms_come_from_top_passages(self):
        results = self.index.search("tunnel")
        terms = self.index.expansion_terms("tunnel", results)
        self.assertTrue(terms)
        self.assertNotIn("tunnel", terms)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.index = BM25Index(PASSAGES)

    def test_answer_kind_bonus_prefers_matching_figures(self):
        results = self.index.search("tunnel project")
        plain = score_sentences(self.index, "how long will the tunnel project take", results)
        typed = score_sentences(self.index, "how long will the tunnel project take", results, answer_kind="duration")
        self.assertIn("48 months", typed[0].text)
        best_plain = {e.text: e.score for e in plain}
        self.assertGreater(typed[0].score, best_plain.get(typed[0].text, 0))

    def test_select_evidence_removes_near_duplicates_and_weak_items(self):
        terms = {"tunnel", "cost", "billion"}
        cands = [
            EvidenceSentence("The tunnel costs $2 billion.", PASSAGES[0], 0.9, 1.0, set(terms)),
            EvidenceSentence("Tunnel cost: $2 billion.", PASSAGES[3], 0.85, 1.0, set(terms)),
            EvidenceSentence("Engineers expect 48 months.", PASSAGES[2], 0.6, 0.5, {"engineer", "expect", "month"}),
            EvidenceSentence("Bread is sold early.", PASSAGES[1], 0.1, 0.1, {"bread"}),
        ]
        chosen = select_evidence(cands)
        self.assertEqual([c.text for c in chosen], ["The tunnel costs $2 billion.", "Engineers expect 48 months."])

    def test_headings_are_not_evidence(self):
        index = BM25Index([passage(0, "a", "Tunnel costs\n\nThe tunnel will cost $2 billion to build.")])
        found = score_sentences(index, "tunnel costs", index.search("tunnel costs"))
        self.assertEqual([e.text for e in found], ["The tunnel will cost $2 billion to build."])


if __name__ == "__main__":
    unittest.main()
