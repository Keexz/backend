import re
from dataclasses import dataclass

# Placeholders are intentionally unlikely to be produced by Ryne.
NUM_PLACEHOLDER_FMT = "[[NUM_{idx}]]"
EQ_PLACEHOLDER_FMT = "[[EQ_{idx}]]"
PLACEHOLDER_RE = re.compile(r"\[\[(?:NUM|EQ)_\d+\]\]")

# Equation patterns (applied before number masking to avoid splitting equations)
# 1. LaTeX inline $...$  (non-greedy)
_EQ_DOLLAR_RE = re.compile(r"\$[^$]{1,300}?\$")
# 2. Bracket math with digits and operators, e.g. "E = mc^2", "a = b + c", "3*x + 2 = 7"
#    Heuristic: sequence containing digits/operators/= and at least one operator/=, length >=3
_EQ_OPERATOR_RE = re.compile(
    r"(?<![\w$])(?:[\w.\s]*\d[\w.\s]*[+\-*/=^<>]+\s*[\w\d\s+\-*/=^<>()]+)"
)

# Number tokens: standalone numbers, decimals, comma-separated, percentages
_NUM_RE = re.compile(r"\b\d+(?:[.,]\d+)*%?\b")


@dataclass(frozen=True)
class MaskResult:
    masked_text: str
    mapping: dict[str, str]  # placeholder -> original
    is_equation_only: bool


def mask_numbers_and_equations(text: str) -> MaskResult:
    """
    Replace numeric tokens and equation-like substrings with placeholders.
    Order: equations first (so numbers inside equations are preserved as part of equation placeholder),
    then remaining numbers.
    Returns mapping to restore after humanization.
    """
    if not text or not text.strip():
        return MaskResult(masked_text=text, mapping={}, is_equation_only=False)

    mapping: dict[str, str] = {}
    masked = text

    # Collect equation spans (dollar first)
    eq_spans: list[tuple[int, int, str]] = []

    for m in _EQ_DOLLAR_RE.finditer(masked):
        eq_spans.append((m.start(), m.end(), m.group()))

    # Operator equations: scan after dollar masking? Use original text for matching,
    # but we need to avoid overlapping dollar regions.
    # Simpler: match on current masked (which still has original equation text except dollar already considered)
    # We'll match operator equations on original text and skip if overlaps dollar.
    dollar_ranges = [(s, e) for s, e, _ in eq_spans]
    for m in _EQ_OPERATOR_RE.finditer(text):
        s, e = m.start(), m.end()
        val = m.group().strip()
        if len(val) < 4:
            continue
        # Must contain at least one digit or operator that suggests equation
        if not any(ch in val for ch in "=+-*/^<>"):
            continue
        if any(s < dr_e and e > dr_s for dr_s, dr_e in dollar_ranges):
            continue
        # Avoid sentences that are just numbers+operators but with leading letters? Keep
        eq_spans.append((s, e, val))

    # Deduplicate overlapping spans (prefer longer, earlier)
    eq_spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    non_overlapping: list[tuple[int, int, str]] = []
    last_end = -1
    for s, e, v in eq_spans:
        if s >= last_end:
            non_overlapping.append((s, e, v))
            last_end = e

    # Replace equations in reverse order to keep offsets stable
    # Need to sort by start descending for replacement
    for idx, (s, e, val) in enumerate(sorted(non_overlapping, key=lambda x: -x[0])):
        placeholder = EQ_PLACEHOLDER_FMT.format(idx=idx)
        # Map placeholder -> original substring as it appears in current masked text
        # Since we replace in descending order, slice positions remain valid
        # We use original offsets on the progressively masked string? To keep simple, redo find on masked
        # Instead generate placeholders sequentially and replace via regex substitution
        pass

    # Easier: use regex substitution with counters
    mapping.clear()
    eq_counter = 0

    def _eq_dollar_repl(m: re.Match) -> str:
        nonlocal eq_counter
        ph = EQ_PLACEHOLDER_FMT.format(idx=eq_counter)
        mapping[ph] = m.group()
        eq_counter += 1
        return ph

    masked = _EQ_DOLLAR_RE.sub(_eq_dollar_repl, masked)

    # Operator equations after dollar: apply on masked (which now has placeholders)
    # To avoid re-matching placeholders, pattern excludes brackets
    def _eq_op_repl(m: re.Match) -> str:
        nonlocal eq_counter
        val = m.group().strip()
        if len(val) < 4 or not any(ch in val for ch in "=+-*/^<>"):
            return val
        # Don't mask if it looks like plain sentence with a few numbers but not equation-dense
        # Heuristic: if placeholder already present skip
        if "[[" in val:
            return val
        ph = EQ_PLACEHOLDER_FMT.format(idx=eq_counter)
        mapping[ph] = val
        eq_counter += 1
        return ph

    masked = _EQ_OPERATOR_RE.sub(_eq_op_repl, masked)

    num_counter = 0

    def _num_repl(m: re.Match) -> str:
        nonlocal num_counter
        ph = NUM_PLACEHOLDER_FMT.format(idx=num_counter)
        mapping[ph] = m.group()
        num_counter += 1
        return ph

    masked = _NUM_RE.sub(_num_repl, masked)

    # Detect equation/number-only sentence: after removing placeholders and stripping
    # punctuation/operators/spaces, if no alphabetic letters remain, skip.
    temp = PLACEHOLDER_RE.sub("", masked)
    # Remove common math/punctuation/numbers remnants
    temp_letters = re.sub(r"[^A-Za-z]", "", temp)
    is_equation_only = len(temp_letters.strip()) == 0 and bool(mapping)

    return MaskResult(masked_text=masked, mapping=mapping, is_equation_only=is_equation_only)


def unmask_text(text: str, mapping: dict[str, str]) -> str:
    """Restore placeholders to original values. Placeholders not in mapping are left as-is."""
    if not mapping:
        return text
    result = text
    for ph, original in mapping.items():
        result = result.replace(ph, original)
    return result
