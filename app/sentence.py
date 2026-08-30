import re
from dataclasses import dataclass

ABBREVIATIONS = {
    "e.g.",
    "i.e.",
    "etc.",
    "vs.",
    "mr.",
    "mrs.",
    "mr",
    "mrs",
    "ms.",
    "dr.",
    "prof.",
    "sr.",
    "jr.",
    "st.",
    "u.s.",
    "u.k.",
    "u.n.",
    "b.sc.",
    "m.sc.",
    "ph.d.",
}

# Lowercase set for fast check after stripping punctuation
_ABBREV_SET = {a.lower() for a in ABBREVIATIONS}


@dataclass(frozen=True)
class SentenceSpan:
    text: str
    start: int
    end: int


_SENTENCE_END_RE = re.compile(r"[.!?]+[\"')\]]*")


def segment_sentences(text: str) -> list[SentenceSpan]:
    """
    Split paragraph text into sentences, respecting abbreviations.
    Returns spans with original offsets (including trailing spaces trimmed).
    Empty / whitespace-only input returns [].
    """
    if not text or not text.strip():
        return []

    spans: list[SentenceSpan] = []
    length = len(text)
    start = 0
    i = 0

    # Find all candidate sentence boundaries (punctuation followed by space or end)
    candidates: list[int] = []
    for m in _SENTENCE_END_RE.finditer(text):
        end = m.end()
        # Boundary is valid only if followed by whitespace or end of string
        # e.g. "Hello world. Next" -> boundary at '.'
        # "3.14" -> '.' followed by digit, not a boundary
        if end >= length or text[end].isspace():
            # Check abbreviation guard: look back to word containing the period
            # Take substring from start of current candidate span to end
            candidates.append(end)

    # Filter candidates that are inside abbreviations
    filtered: list[int] = []
    for end in candidates:
        # Extract token ending at end (backwards until space)
        token_start = text.rfind(" ", 0, end) + 1  # 0 if no space
        token = text[token_start:end].strip().lower()
        # token includes the ending punctuation, e.g. "e.g."
        if token in _ABBREV_SET:
            continue
        # Also check single-letter abbreviation like "U.S." already covered,
        # but handle "Dr." case where token is "dr."
        if token in _ABBREV_SET:
            continue
        filtered.append(end)

    # Build spans using filtered boundaries
    prev = 0
    for end in filtered:
        # Include trailing whitespace handling: span is [prev, end) trimmed
        raw = text[prev:end]
        # Extend to consume following whitespace for next start, but not include in span
        next_start = end
        while next_start < length and text[next_start].isspace():
            next_start += 1
        trimmed = raw.strip()
        if trimmed:
            # Find actual start of trimmed text within raw
            lstrip_len = len(raw) - len(raw.lstrip())
            actual_start = prev + lstrip_len
            actual_end = actual_start + len(trimmed)
            spans.append(SentenceSpan(text=trimmed, start=actual_start, end=actual_end))
        prev = next_start
        if prev >= length:
            break

    # Tail after last boundary
    if prev < length:
        tail = text[prev:].strip()
        if tail:
            lstrip_len = len(text[prev:]) - len(text[prev:].lstrip())
            actual_start = prev + lstrip_len
            actual_end = actual_start + len(tail)
            spans.append(SentenceSpan(text=tail, start=actual_start, end=actual_end))

    # Fallback: if no spans but text exists (no punctuation), return whole text
    if not spans and text.strip():
        stripped = text.strip()
        start_off = len(text) - len(text.lstrip())
        spans.append(SentenceSpan(text=stripped, start=start_off, end=start_off + len(stripped)))

    return spans
