"""Small, dependency-free text utilities: tokenising, stemming, sentence splitting
and figure extraction. Deterministic so that results and tests are reproducible."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each either else
    ever every few for from further had has have having he her here hers herself him himself his
    how i if in into is it its itself just let me more most my myself no nor not now of off on
    once only or other our ours ourselves out over own per please same she should so some such
    than that the their theirs them themselves then there these they this those through to too
    under until up upon us very via was we were what when where which while who whom whose why
    will with within without would yet you your yours yourself yourselves tell explain describe
    give show find list summarise summarize according much many say said says regarding main key major
    important primary
    """.split()
)

QUESTION_WORDS = frozenset({"what", "how", "why", "when", "where", "who", "which", "whom", "whose"})

_WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "dr.", "mr.", "mrs.", "ms.", "st.", "no.", "approx.", "inc.", "ltd.")


def normalize(text: str) -> str:
    """Normalise unicode, collapse whitespace and drop control characters."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def stem(word: str) -> str:
    """A deliberately light suffix stripper (good enough for ranking, never shown to users)."""
    if len(word) <= 3 or word.isdigit():
        return word
    for suffix, replacement, min_len in (
        ("ies", "y", 5),
        ("sses", "ss", 5),
        ("ations", "ate", 7),
        ("ation", "ate", 6),
        ("ings", "", 6),
        ("ing", "", 6),
        ("edly", "", 6),
        ("ed", "", 5),
        ("ly", "", 5),
        ("es", "", 5),
        ("s", "", 4),
    ):
        if word.endswith(suffix) and len(word) >= min_len and not word.endswith("ss"):
            return word[: len(word) - len(suffix)] + replacement
    return word


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def tokenize(text: str) -> list[str]:
    """Lower-cased, stop-word-free, stemmed content tokens."""
    return [stem(w) for w in words(text) if w not in STOPWORDS and w not in QUESTION_WORDS]


def content_terms(text: str) -> set[str]:
    """Content tokens excluding pure numbers (used for topical overlap checks)."""
    return {t for t in tokenize(text) if not t.replace(".", "").replace(",", "").isdigit() and len(t) > 1}


def separate_headings(text: str) -> str:
    """Give short unpunctuated lines (titles, headings, labels) their own paragraph.

    Used for plain text and Markdown, where a heading followed by a single newline would
    otherwise be glued onto the first sentence of the next paragraph.
    """
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        stripped = line.strip()
        previous_blank = i == 0 or not lines[i - 1].strip()
        following = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if (
            stripped
            and previous_blank
            and following[:1].isupper()
            and len(stripped.split()) < 12
            and not stripped.endswith((".", "!", "?", ",", ";", ":"))
        ):
            out.append("")
    return "\n".join(out)


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping common abbreviations intact."""
    protected = text
    for i, abbr in enumerate(_ABBREVIATIONS):
        protected = re.sub(re.escape(abbr), f"\u0000{i}\u0000", protected, flags=re.IGNORECASE)
    parts: list[str] = []
    for block in re.split(r"\n\s*\n|\n(?=\s*[-*•]\s)", protected):
        block = block.replace("\n", " ").strip()
        if not block:
            continue
        parts.extend(re.split(r"(?<=[.!?])[\"”’)]?\s+(?=[\"“(]?[A-Z0-9])", block))
    sentences = []
    for part in parts:
        restored = re.sub(r"\u0000(\d+)\u0000", lambda m: _ABBREVIATIONS[int(m.group(1))], part)
        restored = re.sub(r"^[-*•]\s+", "", restored.strip())
        if len(restored) >= 3:
            sentences.append(restored)
    return sentences


# ---------------------------------------------------------------------------
# Figures (numbers with units) for conflict detection
# ---------------------------------------------------------------------------

_MONTHS = (
    "january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec"
).split()

_NUMBER_RE = re.compile(
    r"(?P<cur>[$€£₹])?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?:-|–|to)\s?(?P<num2>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?))?"
    r"\s?(?P<scale>million|billion|thousand|bn|m|k)?\b"
    r"\s?(?P<unit>%|percent\b|per cent\b|months?\b|years?\b|weeks?\b|days?\b)?",
    re.IGNORECASE,
)

_SCALES = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9}
_DURATION_IN_MONTHS = {"day": 1 / 30.4, "week": 1 / 4.345, "month": 1.0, "year": 12.0}


@dataclass(frozen=True)
class Figure:
    """A number found in a sentence, normalised so that comparable figures can be compared."""

    kind: str  # currency | percent | duration | count
    unit: str  # e.g. "$", "%", "months", or the noun that follows a count
    low: float
    high: float
    raw: str
    context: frozenset[str]

    @property
    def is_range(self) -> bool:
        return self.high != self.low

    def overlaps(self, other: Figure, tolerance: float = 0.02) -> bool:
        span_self = (self.low * (1 - tolerance), self.high * (1 + tolerance))
        span_other = (other.low * (1 - tolerance), other.high * (1 + tolerance))
        return span_self[0] <= span_other[1] and span_other[0] <= span_self[1]


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


_GENERIC_CONTEXT = frozenset(
    {
        "about",
        "around",
        "approximately",
        "roughly",
        "nearly",
        "total",
        "estimated",
        "expected",
        "least",
        "over",
        "under",
        "only",
        "plus",
        "including",
    }
)


def _local_context(before: str, after: str, span: int = 4) -> frozenset[str]:
    """The content words immediately around a figure: what the number is *about*."""

    def content(text: str) -> list[str]:
        return [stem(w) for w in words(text) if w not in STOPWORDS and w not in _GENERIC_CONTEXT and not w[0].isdigit()]

    return frozenset(content(before)[-span:] + content(after)[:span])


def extract_figures(sentence: str) -> list[Figure]:
    """Find currency amounts, percentages, durations and counts in a sentence."""
    figures: list[Figure] = []
    lowered = sentence.lower()
    for match in _NUMBER_RE.finditer(sentence):
        num = match.group("num")
        cur = match.group("cur")
        scale_raw = (match.group("scale") or "").lower()
        unit_raw = (match.group("unit") or "").lower()
        start, end = match.span()
        before = lowered[max(0, start - 12) : start]
        after_text = lowered[end : end + 40]
        # Skip calendar dates and plain years: "March 2027", "12 November", "in 2024".
        if any(re.search(rf"\b{m}\.?\s*$", before) for m in _MONTHS):
            continue
        if any(re.match(rf"\s*(?:of\s+)?{m}\b", after_text) for m in _MONTHS):
            continue
        value = _to_float(num)
        if not cur and not scale_raw and not unit_raw and "." not in num and 1900 <= value <= 2100:
            continue
        # A lone "m" right after a number without a currency is usually metres, not millions.
        if scale_raw in {"m", "k"} and not cur:
            continue
        multiplier = _SCALES.get(scale_raw, 1.0)
        low = value * multiplier
        high = _to_float(match.group("num2")) * multiplier if match.group("num2") else low
        if high < low:
            low, high = high, low

        context = _local_context(lowered[:start], lowered[end:])

        if cur or re.match(r"\s*(?:dollars|euros|pounds|rupees)\b", after_text):
            kind, unit = "currency", cur or "$"
        elif unit_raw in {"%", "percent", "per cent"}:
            kind, unit = "percent", "%"
        elif unit_raw:
            base = unit_raw.rstrip("s")
            factor = _DURATION_IN_MONTHS[base]
            kind, unit = "duration", "months"
            low, high = round(low * factor, 2), round(high * factor, 2)
        else:
            follower = re.match(r"\s*([a-z][a-z-]{2,})", after_text)
            if not follower or follower.group(1) in STOPWORDS:
                continue
            kind, unit = "count", stem(follower.group(1))
        figures.append(Figure(kind, unit, low, high, match.group(0).strip(), context))
    return figures


INJECTION_PATTERNS = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions|prompts?|rules)"
    r"|you\s+are\s+now\s+(a|an|the)\b"
    r"|system\s+prompt"
    r"|<\s*/?\s*(system|assistant|instructions?)\s*>",
    re.IGNORECASE,
)


def looks_like_injection(text: str) -> bool:
    """Heuristic flag for text that addresses an AI model with instructions."""
    return bool(INJECTION_PATTERNS.search(text))
