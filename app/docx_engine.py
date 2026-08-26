import io
from dataclasses import dataclass, field

from docx import Document
from docx.document import Document as DocumentObject
from docx.text.paragraph import Paragraph

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

EXACT_SECTION_TITLES = {
    "TITLE",
    "TITLE PAGE",
    "DECLARATION",
    "CERTIFICATION",
    "CERTIFICATE",
    "DEDICATION",
    "ACKNOWLEDGEMENT",
    "ACKNOWLEDGEMENTS",
    "ACKNOWLEDGMENT",
    "ACKNOWLEDGMENTS",
    "TABLE OF CONTENTS",
    "CONTENTS",
    "LIST OF TABLES",
    "LIST OF FIGURES",
    "APPENDIX",
    "APPENDICES",
    "BIBLIOGRAPHY",
    "REFERENCE",
    "REFERENCES",
}

PREFIXABLE_SECTION_TITLES = ("APPENDIX", "APPENDICES", "REFERENCES")

HEADING_STYLES = {"heading 1", "heading 2"}

MAX_TITLE_LENGTH = 60

IGNORED_HIGHLIGHT_VALUES = {"none"}

IGNORED_SHADING_FILLS = {"", "auto", "ffffff"}


@dataclass
class ParagraphInfo:
    index: int
    text: str
    style_name: str
    has_highlight: bool
    is_heading: bool
    is_section_title: bool
    is_protected: bool
    protection_reason: str
    classified_by: str = "rules"


@dataclass
class DocumentAnalysis:
    paragraphs: list[ParagraphInfo] = field(default_factory=list)

    @property
    def humanizable_indices(self) -> list[int]:
        return [
            p.index
            for p in self.paragraphs
            if p.has_highlight and not p.is_protected
        ]


def load_document(data: bytes) -> DocumentObject:
    return Document(io.BytesIO(data))


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip(" .:;-").upper()


def _is_heading(paragraph: Paragraph) -> bool:
    style_name = ""
    if paragraph.style is not None:
        style_name = (paragraph.style.name or "").lower()
    return style_name in HEADING_STYLES


def _is_section_title(paragraph: Paragraph) -> bool:
    normalized = _normalize(paragraph.text)
    if not normalized or len(normalized) > MAX_TITLE_LENGTH:
        return False
    if normalized in EXACT_SECTION_TITLES:
        return True
    return any(
        normalized.startswith(prefix + " ") or normalized.startswith(prefix + ":")
        for prefix in PREFIXABLE_SECTION_TITLES
    )


def paragraph_has_highlight(paragraph: Paragraph) -> bool:
    for run_element in paragraph._p.findall(f".//{{{W_NS}}}r"):
        rpr = run_element.find(f"{{{W_NS}}}rPr")
        if rpr is None:
            continue
        for node in rpr.findall(f"{{{W_NS}}}highlight"):
            value = (node.get(f"{{{W_NS}}}val") or "").lower()
            if value and value not in IGNORED_HIGHLIGHT_VALUES:
                return True
        for node in rpr.findall(f"{{{W_NS}}}shd"):
            fill = (node.get(f"{{{W_NS}}}fill") or "").lower()
            if fill not in IGNORED_SHADING_FILLS:
                return True
    return False


def resolve_protection(paragraphs: list[ParagraphInfo]) -> None:
    seen_boundary = False
    in_protected_scope = False

    for info in paragraphs:
        info.protection_reason = ""
        if info.is_section_title:
            seen_boundary = True
            in_protected_scope = True
            info.protection_reason = "section-title"
        elif info.is_heading:
            seen_boundary = True
            in_protected_scope = False
            info.protection_reason = "heading"
        elif in_protected_scope:
            info.protection_reason = "protected-section"
        elif not seen_boundary:
            info.protection_reason = "front-matter"
        info.is_protected = bool(info.protection_reason)


def analyze_document(document: DocumentObject) -> DocumentAnalysis:
    analysis = DocumentAnalysis()

    for index, paragraph in enumerate(document.paragraphs):
        analysis.paragraphs.append(
            ParagraphInfo(
                index=index,
                text=paragraph.text,
                style_name=paragraph.style.name if paragraph.style else "",
                has_highlight=paragraph_has_highlight(paragraph),
                is_heading=_is_heading(paragraph),
                is_section_title=_is_section_title(paragraph),
                is_protected=False,
                protection_reason="",
            )
        )

    resolve_protection(analysis.paragraphs)
    return analysis
