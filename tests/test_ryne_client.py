import json

import httpx
import pytest

from app.ryne_client import HumanizeError, HumanizeResult, humanize_text

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
