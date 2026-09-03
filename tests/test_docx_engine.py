import zipfile

import pytest

from app.docx_engine import (
    _is_section_title,
    analyze_document,
    load_document,
    paragraph_has_asterisk,
    strip_candidate_marker_characters,
    strip_asterisk_markers,
)


def _analyze(build_docx, specs):
    return analyze_document(load_document(build_docx(specs)))


def test_single_asterisk_paragraph_is_humanizable(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "Plain intro sentence."},
            {"text": "*This finding was significant.*"},
        ],
    )
    assert analysis.humanizable_indices == [2]


@pytest.mark.parametrize(
    "text",
    [
        "**Double markers are ignored.**",
        "*Unclosed marker is ignored.",
        "Closing marker without an opener.*",
        "****",
    ],
)
def test_non_single_marker_patterns_are_ignored(build_docx, text):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": text},
        ],
    )
    assert analysis.humanizable_indices == []


def test_legacy_highlight_and_shading_are_ignored(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "Highlighted text.", "highlighted": True},
            {"text": "Shaded text.", "shade_fill": "B9E8F0"},
        ],
    )
    assert analysis.humanizable_indices == []


def test_single_markers_split_across_runs_are_detected(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {
                "runs": [
                    ("Plain sentence. This ", False),
                    ("*", False),
                    ("marked phrase", False),
                    ("*", False),
                    (" is selected. Final plain sentence.", False),
                ]
            },
        ],
    )
    assert analysis.humanizable_indices == [1]
    assert analysis.humanizable_sentence_indices == [2]


def test_partial_marker_selects_whole_sentence(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {
                "runs": [
                    ("Human-written start. AI-generated ", False),
                    ("*continuation*", False),
                    (" remains in this sentence.", False),
                ]
            },
        ],
    )
    info = analysis.paragraphs[1]
    assert info.has_highlight is True
    assert info.is_protected is False
    assert info.text == "Human-written start. AI-generated *continuation* remains in this sentence."
    assert analysis.humanizable_indices == [1]
    assert analysis.humanizable_sentence_indices == [2]


def test_marker_stripping_only_removes_strict_single_pairs():
    text = "Keep **double**, remove *single text*, and keep *unclosed."
    assert strip_asterisk_markers(text) == "Keep **double**, remove single text, and keep *unclosed."


def test_one_marker_pair_can_select_multiple_sentences(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "*First marked sentence. Second marked sentence.*"},
        ],
    )
    assert analysis.humanizable_sentence_indices == [1, 2]
    assert strip_candidate_marker_characters(analysis.sentences[1].text) == "First marked sentence."
    assert strip_candidate_marker_characters(analysis.sentences[2].text) == "Second marked sentence."


def test_front_matter_before_first_boundary_is_protected(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "THE POLITICS OF HUMANITARIAN AID IN INTERNATIONAL CONFLICTS"},
            {"text": "BY"},
            {"text": "JUNE, 2026"},
            {"text": "DECLARATION"},
            {"text": "*I hereby declare this work is original.*"},
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "*Body text of chapter one.*"},
        ],
    )
    assert all(info.is_protected for info in analysis.paragraphs[:6])
    assert analysis.humanizable_indices == [6]


def test_protection_extends_until_next_heading(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE", "style": "Heading 1"},
            {"text": "*Intro paragraph.*"},
            {"text": "REFERENCES"},
            {"text": "*Adams, J. (2020). Some book title.*"},
            {"text": "CHAPTER TWO", "style": "Heading 1"},
            {"text": "*Next chapter content.*"},
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


def test_normal_style_chapter_text_is_not_a_boundary(build_docx):
    analysis = _analyze(
        build_docx,
        [
            {"text": "CHAPTER ONE"},
            {"text": "*Marked body sentence.*"},
        ],
    )
    assert analysis.paragraphs[0].is_heading is False
    assert analysis.paragraphs[1].protection_reason == "front-matter"
    assert analysis.humanizable_sentence_indices == []


def test_no_asterisk_markers_yields_empty_worklist(build_docx):
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
    data = build_docx_with_table("*Marked text inside a table cell.*")
    analysis = analyze_document(load_document(data))
    assert len(analysis.paragraphs) == 0
    assert analysis.humanizable_indices == []


def test_paragraph_has_asterisk_ignores_double_markers(build_docx):
    document = load_document(build_docx([{"text": "**Double only.**"}]))
    assert paragraph_has_asterisk(document.paragraphs[0]) is False


def test_corrupted_file_raises():
    with pytest.raises(zipfile.BadZipFile):
        load_document(b"this is definitely not a docx file")
