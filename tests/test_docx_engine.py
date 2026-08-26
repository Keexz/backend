import zipfile

import pytest

from app.docx_engine import _is_section_title, analyze_document, load_document, paragraph_has_highlight


def _analyze(build_docx, specs):
    return analyze_document(load_document(build_docx(specs)))


def test_blue_paragraph_is_humanizable(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "Plain intro sentence."},
            {"text": "This finding was significant.", "highlighted": True},
        ],
    )
    assert analysis.humanizable_indices == [2]


def test_any_marker_color_is_detected(build_docx):
    for color in ("cyan", "yellow", "green", "darkMagenta"):
        analysis = _analyze(
            build_docx,
            [
                {"text": "CHAPTER ONE", "style": "Heading 1"},
                {"text": f"Marked in {color}.", "highlight_val": color},
            ],
        )
        assert analysis.humanizable_indices == [1], color


def test_character_shading_is_detected(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "Turnitin-style marked text.", "shade_fill": "B9E8F0"},
        ],
    )
    assert analysis.humanizable_indices == [1]


def test_white_shading_and_none_highlight_are_ignored(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "White shading.", "shade_fill": "FFFFFF"},
            {"text": "Explicit none.", "highlight_val": "none"},
            {"text": "Plain text."},
        ],
    )
    assert analysis.humanizable_indices == []


def test_partial_highlight_marks_whole_paragraph(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {
                "runs": [
                    ("Human-written start. ", False),
                    ("AI-generated continuation.", True),
                ]
            },
        ],
    )
    info = analysis.paragraphs[1]
    assert info.has_highlight is True
    assert info.is_protected is False
    assert info.text == "Human-written start. AI-generated continuation."
    assert analysis.humanizable_indices == [1]


def test_front_matter_before_first_boundary_is_protected(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "THE POLITICS OF HUMANITARIAN AID IN INTERNATIONAL CONFLICTS"},
            {"text": "BY"},
            {"text": "JUNE, 2026"},
            {"text": "DECLARATION"},
            {"text": "I hereby declare this work is original.", "highlighted": True},
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "Body text of chapter one.", "highlighted": True},
        ],
    )
    assert all(info.is_protected for info in analysis.paragraphs[:6])
    assert analysis.humanizable_indices == [6]


def test_protection_extends_until_next_heading(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "Intro paragraph.", "highlighted": True},
            {"text": "REFERENCES"},
            {"text": "Adams, J. (2020). Some book title.", "highlighted": True},
            {"text": "CHAPTER TWO", "style": "Heading 1"},
            {"text": "Next chapter content.", "highlighted": True},
        ],
    )
    references_info = analysis.paragraphs[3]
    assert references_info.is_protected is True
    assert references_info.protection_reason == "protected-section"
    assert analysis.humanizable_indices == [1, 5]


def test_headings_are_always_protected(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "Section title", "style": "Heading 2"},
            {"text": "Normal body."},
        ],
    )
    assert all(info.is_protected for info in analysis.paragraphs[:2])
    assert analysis.paragraphs[0].protection_reason == "heading"
    assert analysis.paragraphs[1].protection_reason == "heading"


def test_no_highlights_yields_empty_worklist(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "First paragraph."},
            {"text": "Second paragraph."},
        ],
    )
    assert analysis.humanizable_indices == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("DECLARATION", True),
        ("Table of Contents", True),
        ("Acknowledgements", True),
        ("Appendix A: Research Instruments", True),
        ("References:", True),
        ("Referencing styles vary across journals.", False),
        ("The declaration was signed yesterday.", False),
        ("", False),
    ],
)
def test_section_title_matching(text, expected):
    from unittest.mock import MagicMock

    paragraph = MagicMock()
    paragraph.text = text
    assert _is_section_title(paragraph) is expected


def test_table_content_is_never_analyzed(build_docx_with_table):
    data = build_docx_with_table("Blue text inside a table cell.")
    analysis = analyze_document(load_document(data))
    assert len(analysis.paragraphs) == 0
    assert analysis.humanizable_indices == []


def test_corrupted_file_raises():
    with pytest.raises(zipfile.BadZipFile):
        load_document(b"this is definitely not a docx file")
