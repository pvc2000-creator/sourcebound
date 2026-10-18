"""BM25 passage retrieval and sentence-level evidence scoring (no external services)."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .text import extract_figures, split_sentences, stem, tokenize

# Words that signal the *type* of answer rather than its topic ("how long", "how much").
INTENT_TERMS = frozenset(stem(w) for w in ("long", "much", "many", "take", "takes", "often", "big", "large", "far", "soon"))


@dataclass(frozen=True)
class Passage:
    chunk_id: str
    source_id: str
    source_title: str
    page: int | None
    text: str


@dataclass
class ScoredPassage:
    passage: Passage
    score: float


@dataclass
class EvidenceSentence:
    text: str
    passage: Passage
    score: float
    coverage: float
    terms: set[str] = field(default_factory=set)


class BM25Index:
    def __init__(self, passages: list[Passage], k1: float = 1.4, b: float = 0.75) -> None:
        self.passages = passages
        self.k1 = k1
        self.b = b
        self.doc_tokens = [tokenize(p.text) for p in passages]
        self.doc_tf = [Counter(tokens) for tokens in self.doc_tokens]
        self.doc_len = [len(tokens) for tokens in self.doc_tokens]
        self.avg_len = (sum(self.doc_len) / len(self.doc_len)) if passages else 0.0
        df: Counter[str] = Counter()
        for tf in self.doc_tf:
            df.update(tf.keys())
        n = len(passages)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def __len__(self) -> int:
        return len(self.passages)

    def term_idf(self, term: str) -> float:
        return self.idf.get(term, 0.0)

    def search(self, query: str, k: int = 8) -> list[ScoredPassage]:
        terms = tokenize(query)
        if not terms or not self.passages:
            return []
        results: list[ScoredPassage] = []
        for i, tf in enumerate(self.doc_tf):
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * self.doc_len[i] / (self.avg_len or 1))
            for term in set(terms):
                freq = tf.get(term)
                if freq:
                    score += self.idf[term] * freq * (self.k1 + 1) / (freq + norm)
            if score > 0:
                results.append(ScoredPassage(self.passages[i], score))
        results.sort(key=lambda r: (-r.score, r.passage.chunk_id))
        return results[:k]

    def expansion_terms(self, query: str, top: list[ScoredPassage], n: int = 4) -> list[str]:
        """Pseudo-relevance feedback: frequent, distinctive terms in the best passages."""
        query_terms = set(tokenize(query))
        weights: dict[str, float] = {}
        for rank, item in enumerate(top[:3]):
            for term, freq in Counter(tokenize(item.passage.text)).items():
                if term in query_terms or len(term) < 4 or term.isdigit():
                    continue
                weights[term] = weights.get(term, 0.0) + freq * self.term_idf(term) / (rank + 1)
        return [term for term, _ in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def score_sentences(index: BM25Index, query: str, passages: list[ScoredPassage], answer_kind: str | None = None) -> list[EvidenceSentence]:
    """Rank individual sentences from retrieved passages by idf-weighted query-term coverage.

    When the question expects a particular kind of figure (a duration for "how long", an
    amount of money for "how much does it cost"), sentences containing one get a bonus.
    """
    q_terms = set(tokenize(query)) - INTENT_TERMS
    if not q_terms or not passages:
        return []
    total_weight = sum(max(index.term_idf(t), 0.2) for t in q_terms)
    top_score = passages[0].score or 1.0
    seen: set[tuple[str, str]] = set()
    scored: list[EvidenceSentence] = []
    for item in passages:
        passage_terms = set(tokenize(item.passage.text))
        for sentence in split_sentences(item.passage.text):
            key = (item.passage.source_id, sentence)
            n_words = len(sentence.split())
            if key in seen or n_words < 5:
                continue
            if n_words < 10 and not sentence.rstrip('"”’)').endswith((".", "!", "?")):
                continue  # headings, titles and labels are not evidence
            seen.add(key)
            s_terms = set(tokenize(sentence))
            matched = q_terms & s_terms
            nearby = (q_terms & passage_terms) - matched  # e.g. the section heading names the topic
            has_kind = bool(answer_kind) and any(f.kind == answer_kind for f in extract_figures(sentence))
            if not matched and not has_kind and len(nearby) < max(1, len(q_terms) // 2):
                continue
            weight = sum(max(index.term_idf(t), 0.2) for t in matched)
            weight += 0.4 * sum(max(index.term_idf(t), 0.2) for t in nearby)
            coverage = weight / total_weight
            score = 0.75 * coverage + 0.25 * (item.score / top_score) + (0.25 if has_kind else 0.0)
            scored.append(
                EvidenceSentence(
                    sentence, item.passage, round(score, 4), round(coverage, 4), s_terms | (nearby if matched or has_kind else set())
                )
            )
    scored.sort(key=lambda e: (-e.score, e.passage.chunk_id, e.text))
    return scored


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def select_evidence(candidates: list[EvidenceSentence], limit: int = 4, min_score: float = 0.3) -> list[EvidenceSentence]:
    """Pick strong, non-redundant sentences, preferring a spread across sources."""
    chosen: list[EvidenceSentence] = []
    per_source: Counter[str] = Counter()
    for cand in candidates:
        if cand.score < min_score:
            break
        if any(jaccard(cand.terms, c.terms) > 0.7 for c in chosen):
            continue
        if per_source[cand.passage.source_id] >= 2 and len({c.passage.source_id for c in candidates[:12]}) > 1:
            continue
        chosen.append(cand)
        per_source[cand.passage.source_id] += 1
        if len(chosen) >= limit:
            break
    return chosen
