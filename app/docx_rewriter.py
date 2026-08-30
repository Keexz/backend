from dataclasses import dataclass

from docx.oxml.ns import qn
from docx.shared import Length, RGBColor
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from app.docx_engine import IGNORED_SHADING_FILLS


@dataclass(frozen=True)
class FontStyleSnapshot:
    name: str | None = None
    size: Length | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    color_rgb: RGBColor | None = None


def replace_paragraph_text(paragraph: Paragraph, new_text: str) -> None:
    snapshot = _snapshot_first_marked_font(paragraph)
    if snapshot is None and paragraph.runs:
        snapshot = _snapshot_font(paragraph.runs[0])

    element = paragraph._p
    for child in list(element):
        if child.tag != qn("w:pPr"):
            element.remove(child)

    if not new_text:
        return
    new_run = paragraph.add_run(new_text)
    if snapshot is not None:
        _apply_font_snapshot(new_run, snapshot)
    _strip_marking(new_run)


def replace_paragraph_sentences(
    paragraph: Paragraph,
    sentence_texts: list[str],
    sentence_has_replacement: list[bool] | None = None,
) -> None:
    """
    Sentence-level rewriter. Replaces paragraph content with provided sentence_texts
    (one per segmented sentence, in document order). Sentence-level formatting is
    preserved per-sentence using the first marked run overlapping that sentence,
    fallback to paragraph's first run. Humanized sentences are stripped of marking;
    unchanged sentences keep their original marking intent (but for rebuilt paragraphs
    we strip globally to ensure submission-ready output — protected paragraphs are never rewritten).
    """
    if not sentence_texts:
        return

    from app.sentence import segment_sentences
    from app.docx_engine import get_highlight_ranges, _sentence_has_highlight  # local import to avoid cycle
    from app.sentence import SentenceSpan

    full_text = paragraph.text
    spans = segment_sentences(full_text)
    # If segmentation produced different count than provided texts, fall back to paragraph replacement
    if len(spans) != len(sentence_texts):
        replace_paragraph_text(paragraph, " ".join(sentence_texts))
        return

    # Snapshot per sentence: find first marked run overlapping that span
    # Build highlight ranges and run snapshots mapping
    highlight_ranges = get_highlight_ranges(paragraph)
    # Build per-sentence snapshot list
    snapshots: list[FontStyleSnapshot | None] = []
    # We need run-level snapshots; reuse _snapshot_first_marked_font logic but per sentence
    # Approach: collect runs with their offsets and snapshot
    run_infos: list[tuple[int, int, FontStyleSnapshot, bool]] = []  # start, end, snapshot, is_marked
    offset = 0
    for run in iter_all_runs(paragraph):
        text = run.text or ""
        if not text:
            continue
        is_marked = _run_is_marked(run)
        snap = _snapshot_font(run)
        run_infos.append((offset, offset + len(text), snap, is_marked))
        offset += len(text)

    def snapshot_for_span(span: SentenceSpan) -> FontStyleSnapshot | None:
        # Prefer first marked run overlapping span
        for rs, re, snap, is_marked in run_infos:
            if is_marked and rs < span.end and re > span.start:
                return snap
        for rs, re, snap, _ in run_infos:
            if rs < span.end and re > span.start:
                return snap
        if run_infos:
            return run_infos[0][2]
        return None

    for span in spans:
        snapshots.append(snapshot_for_span(span))

    element = paragraph._p
    for child in list(element):
        if child.tag != qn("w:pPr"):
            element.remove(child)

    for idx, text in enumerate(sentence_texts):
        if idx > 0:
            # Separator space between sentences
            sep_run = paragraph.add_run(" ")
            if snapshots[idx] is not None:
                _apply_font_snapshot(sep_run, snapshots[idx])
            _strip_marking(sep_run)
        run = paragraph.add_run(text)
        snap = snapshots[idx]
        if snap is not None:
            _apply_font_snapshot(run, snap)
        # Humanized sentences should be stripped; for now strip all rebuilt sentences
        # (protected paragraphs are never passed here, so stripping is desired)
        _strip_marking(run)


def iter_all_runs(paragraph: Paragraph):
    for item in paragraph.iter_inner_content():
        if isinstance(item, Run):
            yield item
        else:
            yield from item.runs


def _run_is_marked(run: Run) -> bool:
    if run.font.highlight_color is not None:
        return True
    rpr = run._element.find(qn("w:rPr"))
    if rpr is None:
        return False
    for node in rpr.findall(qn("w:shd")):
        fill = (node.get(qn("w:fill")) or "").lower()
        if fill not in IGNORED_SHADING_FILLS:
            return True
    return False


def _snapshot_first_marked_font(paragraph: Paragraph) -> FontStyleSnapshot | None:
    for run in iter_all_runs(paragraph):
        if _run_is_marked(run):
            return _snapshot_font(run)
    return None


def _snapshot_font(run: Run) -> FontStyleSnapshot:
    font = run.font
    color_rgb = None
    try:
        if font.color is not None and font.color.type is not None:
            color_rgb = font.color.rgb
    except (AttributeError, TypeError):
        color_rgb = None
    return FontStyleSnapshot(
        name=font.name,
        size=font.size,
        bold=font.bold,
        italic=font.italic,
        underline=font.underline,
        color_rgb=color_rgb,
    )


def _strip_marking(run: Run) -> None:
    """Remove highlight and shading marking from a run (submission-ready)."""
    run.font.highlight_color = None
    rpr = run._element.find(qn("w:rPr"))
    if rpr is not None:
        for node in list(rpr.findall(qn("w:shd"))):
            fill = (node.get(qn("w:fill")) or "").lower()
            if fill not in IGNORED_SHADING_FILLS:
                # Remove shading element
                rpr.remove(node)
            else:
                # Also strip white shading remnants
                rpr.remove(node)
        for node in list(rpr.findall(qn("w:highlight"))):
            rpr.remove(node)


def _apply_font_snapshot(run: Run, snapshot: FontStyleSnapshot) -> None:
    font = run.font
    # Don't set highlight here; stripping handles it separately
    if snapshot.name is not None:
        font.name = snapshot.name
    if snapshot.size is not None:
        font.size = snapshot.size
    if snapshot.bold is not None:
        font.bold = snapshot.bold
    if snapshot.italic is not None:
        font.italic = snapshot.italic
    if snapshot.underline is not None:
        font.underline = snapshot.underline
    if snapshot.color_rgb is not None:
        font.color.rgb = snapshot.color_rgb
