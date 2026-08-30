import json

import httpx

from app.docx_engine import analyze_document, load_document
from app.groq_client import (
    apply_boundaries,
    classify_candidates,
    collect_candidates,
)


def _completion_transport(content):
    def handler(request):
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        )

    return httpx.MockTransport(handler)


def _failing_transport():
    def handler(request):
        return httpx.Response(500, json={"error": "internal error"})

    return httpx.MockTransport(handler)


def _sample_analysis(build_docx):
    return analyze_document(
        load_document(
            build_docx(
                [
                    {"text": "CHAPTER ONE", "style": "Heading 1"},
                    {"text": "Intro body paragraph."},
                    {"text": "PREFACE"},
                    {"text": "Preface narrative.", "highlighted": True},
                ]
            )
        )
    )


def test_collect_candidates_selects_only_caps_lines(build_docx):
    analysis = analyze_document(
        load_document(
            build_docx(
                [
                    {"text": "CHAPTER ONE", "style": "Heading 1"},
                    {"text": "PREFACE"},
                    {"text": "This sentence is normal."},
                    {
                        "text": "A VERY LONG LINE OF CAPITAL LETTERS THAT STRETCHES "
                        "FAR BEYOND THE EIGHTY CHARACTER LIMIT SO IT IS IGNORED"
                    },
                    {"text": "12345"},
                    {"text": "It ended with a period."},
                ]
            )
        )
    )
    candidates = collect_candidates(analysis)
    assert [c.text for c in candidates] == ["PREFACE"]


def test_confirmed_boundary_opens_protection(build_docx):
    analysis = _sample_analysis(build_docx)
    candidates = collect_candidates(analysis)
    assert [c.index for c in candidates] == [2]

    boundaries = classify_candidates(
        candidates,
        api_key="test-key",
        model="test-model",
        transport=_completion_transport(json.dumps({"boundaries": [2]})),
    )
    assert boundaries == {2}

    apply_boundaries(analysis, boundaries)
    assert analysis.paragraphs[2].protection_reason == "section-title"
    assert analysis.paragraphs[2].classified_by == "groq"
    assert analysis.humanizable_indices == []


def test_rejected_boundary_leaves_paragraph_humanizable(build_docx):
    analysis = _sample_analysis(build_docx)
    boundaries = classify_candidates(
        collect_candidates(analysis),
        api_key="test-key",
        transport=_completion_transport(json.dumps({"boundaries": []})),
    )
    apply_boundaries(analysis, boundaries)
    assert analysis.humanizable_indices == [3]


def test_api_failure_skips_groq_fallback(build_docx):
    analysis = _sample_analysis(build_docx)
    boundaries = classify_candidates(
        collect_candidates(analysis),
        api_key="test-key",
        transport=_failing_transport(),
    )
    assert boundaries == set()

    apply_boundaries(analysis, boundaries)
    assert analysis.humanizable_indices == [3]


def test_malformed_json_skips_groq_fallback(build_docx):
    analysis = _sample_analysis(build_docx)
    boundaries = classify_candidates(
        collect_candidates(analysis),
        api_key="test-key",
        transport=_completion_transport("this is not json at all"),
    )
    assert boundaries == set()


def test_out_of_range_boundary_indices_are_ignored(build_docx):
    analysis = _sample_analysis(build_docx)
    boundaries = classify_candidates(
        collect_candidates(analysis),
        api_key="test-key",
        transport=_completion_transport(json.dumps({"boundaries": [99, -1]})),
    )
    assert boundaries == set()


def test_empty_candidates_makes_no_network_call():
    def handler(request):
        raise AssertionError("network call must not happen for empty candidates")

    result = classify_candidates([], api_key="k", transport=httpx.MockTransport(handler))
    assert result == set()


def test_already_protected_paragraphs_never_become_groq_classified(build_docx):
    analysis = _sample_analysis(build_docx)
    assert analysis.paragraphs[0].is_protected is True
    apply_boundaries(analysis, {0})
    assert analysis.paragraphs[0].classified_by == "rules"
    assert analysis.paragraphs[0].protection_reason == "heading"
