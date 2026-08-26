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


def _apply_font_snapshot(run: Run, snapshot: FontStyleSnapshot) -> None:
    font = run.font
    font.highlight_color = None
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
