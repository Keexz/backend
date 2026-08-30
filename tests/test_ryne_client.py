import json

import httpx
import pytest

from app.ryne_client import (
    HumanizeError,
    HumanizeResult,
    _split_text_into_chunks,
    humanize_text,
)

ZERO_BACKOFF = (0.0, 0.0, 0.0)


def _capturing_transport(responder):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content.decode("utf-8")))
        return responder(request, len(requests))

    return httpx.MockTransport(handler), requests


def _ok(content="Humanized sentence.", ai_score=3):
    def responder(request, n):
        payload = {"content": content}
        if ai_score is not None:
            payload["aiScore"] = ai_score
        return httpx.Response(200, json=payload)

    return responder


def test_success_returns_content_and_numeric_ai_score():
    transport, requests = _capturing_transport(_ok(ai_score=7))
    result = humanize_text(
        "AI text.",
        api_key="secret-key",
        url="https://ryne.test/api",
        transport=transport,
        backoff_seconds=ZERO_BACKOFF,
    )
    assert result == HumanizeResult(content="Humanized sentence.", ai_score=7.0)

    body = requests[0]
    assert body["text"] == "AI text."
    assert body["tone"] == "professional"
    assert body["purpose"] == "blog post"
    assert body["language"] == "english"
    assert body["user_id"] == "secret-key"
    assert body["shouldStream"] is False
    assert body["beast_mode"] is True


def test_missing_ai_score_returns_none():
    transport, _ = _capturing_transport(_ok(ai_score=None))
    result = humanize_text(
        "t",
        api_key="k",
        url="https://ryne.test/api",
        transport=transport,
        backoff_seconds=ZERO_BACKOFF,
    )
    assert result.ai_score is None


def test_string_percent_ai_score_is_parsed():
    transport, _ = _capturing_transport(_ok(ai_score="12%"))
    result = humanize_text(
        "t",
        api_key="k",
        url="https://ryne.test/api",
        transport=transport,
        backoff_seconds=ZERO_BACKOFF,
    )
    assert result.ai_score == 12.0


def test_unparsable_ai_score_becomes_none():
    transport, _ = _capturing_transport(_ok(ai_score="not-a-score"))
    result = humanize_text(
        "t",
        api_key="k",
        url="https://ryne.test/api",
        transport=transport,
        backoff_seconds=ZERO_BACKOFF,
    )
    assert result.ai_score is None


def test_transient_server_error_recovers_on_retry():
    def responder(request, n):
        if n == 1:
            return httpx.Response(503, json={"error": "overloaded"})
        return _ok()(request, n)

    transport, requests = _capturing_transport(responder)
    result = humanize_text(
        "t",
        api_key="k",
        url="https://ryne.test/api",
        transport=transport,
        backoff_seconds=ZERO_BACKOFF,
    )
    assert result.content == "Humanized sentence."
    assert len(requests) == 2


def test_exhausted_retries_raise_humanize_error():
    def responder(request, n):
        return httpx.Response(500, json={"error": "down"})

    transport, requests = _capturing_transport(responder)
    with pytest.raises(HumanizeError, match="failed after 4 attempts"):
        humanize_text(
            "t",
            api_key="k",
            url="https://ryne.test/api",
            transport=transport,
            backoff_seconds=ZERO_BACKOFF,
        )
    assert len(requests) == 4


def test_non_retryable_auth_error_fails_immediately():
    def responder(request, n):
        return httpx.Response(401, json={"error": "bad key"})

    transport, requests = _capturing_transport(responder)
    with pytest.raises(HumanizeError, match="HTTP 401"):
        humanize_text(
            "t",
            api_key="k",
            url="https://ryne.test/api",
            transport=transport,
            backoff_seconds=ZERO_BACKOFF,
        )
    assert len(requests) == 1


def test_error_detail_extracted_from_response_body():
    def responder(request, n):
        return httpx.Response(
            403,
            json={
                "content": "",
                "status": "INSUFFICIENT_COINS",
                "error_msg": "You don't have enough API coins!",
            },
        )

    transport, requests = _capturing_transport(responder)
    with pytest.raises(HumanizeError, match="You don't have enough API coins"):
        humanize_text(
            "t",
            api_key="k",
            url="https://ryne.test/api",
            transport=transport,
            backoff_seconds=ZERO_BACKOFF,
        )
    assert len(requests) == 1


def test_invalid_json_response_is_retried():
    def responder(request, n):
        if n == 1:
            return httpx.Response(200, text="<html>garbage</html>")
        return _ok()(request, n)

    transport, requests = _capturing_transport(responder)
    result = humanize_text(
        "t",
        api_key="k",
        url="https://ryne.test/api",
        transport=transport,
        backoff_seconds=ZERO_BACKOFF,
    )
    assert result.ai_score == 3
    assert len(requests) == 2


def test_empty_content_is_retried_until_failure():
    transport, requests = _capturing_transport(_ok(content="   ", ai_score=None))
    with pytest.raises(HumanizeError, match="missing content"):
        humanize_text(
            "t",
            api_key="k",
            url="https://ryne.test/api",
            transport=transport,
            backoff_seconds=ZERO_BACKOFF,
        )
    assert len(requests) == 4


def test_large_text_is_chunked():
    sentence = "This is a test sentence. "
    text = sentence * 200

    def responder(request, n):
        return httpx.Response(200, json={"content": f"chunk-{n}"})

    transport, requests = _capturing_transport(responder)
    result = humanize_text(
        text,
        api_key="key",
        url="https://ryne.test/api",
        transport=transport,
        backoff_seconds=ZERO_BACKOFF,
    )

    assert len(requests) > 1
    assert "chunk-1" in result.content
    assert "chunk-2" in result.content
    assert result.content == " ".join(f"chunk-{i}" for i in range(1, len(requests) + 1))


def test_chunk_transient_error_recovers():
    sentence = "This is a test sentence. "
    text = sentence * 200

    def responder(request, n):
        if n == 1:
            return httpx.Response(503, json={"error": "overloaded"})
        return httpx.Response(200, json={"content": f"chunk-{n}"})

    transport, requests = _capturing_transport(responder)
    result = humanize_text(
        text,
        api_key="key",
        url="https://ryne.test/api",
        transport=transport,
        backoff_seconds=ZERO_BACKOFF,
    )

    assert len(requests) == 3
    assert result.content.startswith("chunk-2")
    assert "chunk-3" in result.content


def test_all_chunks_fail_raises_error():
    sentence = "This is a test sentence. "
    text = sentence * 200

    def responder(request, n):
        return httpx.Response(500, json={"error": "down"})

    transport, requests = _capturing_transport(responder)
    with pytest.raises(HumanizeError, match="failed after"):
        humanize_text(
            text,
            api_key="key",
            url="https://ryne.test/api",
            transport=transport,
            backoff_seconds=ZERO_BACKOFF,
        )

    assert len(requests) > 1


def test_split_text_into_chunks_small_text():
    chunks = _split_text_into_chunks("Short text.")
    assert chunks == ["Short text."]


def test_split_text_into_chunks_respects_sentence_boundaries():
    text = "First sentence. Second sentence. Third sentence."
    chunks = _split_text_into_chunks(text, max_chars=20)
    assert len(chunks) > 1
    assert "First" in chunks[0]
    assert any("Second" in c or "Third" in c for c in chunks)


def test_split_text_into_chunks_hard_splits_oversized_sentence():
    long_sentence = "A" * 5000
    chunks = _split_text_into_chunks(long_sentence, max_chars=1000)
    assert len(chunks) == 5
    assert all(len(c) == 1000 for c in chunks[:-1])
