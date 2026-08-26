import logging
import time
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class HumanizeResult:
    content: str
    ai_score: float | None


class HumanizeError(RuntimeError):
    pass


def humanize_text(
    text: str,
    *,
    api_key: str | None = None,
    url: str | None = None,
    transport: httpx.BaseTransport | None = None,
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
) -> HumanizeResult:
    settings = get_settings()
    resolved_key = api_key if api_key is not None else settings.ryne_ai_api_key
    resolved_url = url if url is not None else settings.ryne_ai_url
    payload = {
        "text": text,
        "tone": "professional",
        "purpose": "blog post",
        "language": "english",
        "user_id": resolved_key,
        "shouldStream": False,
        "beast_mode": True,
    }

    last_error = ""
    for attempt in range(len(backoff_seconds) + 1):
        if attempt:
            delay = backoff_seconds[attempt - 1]
            if delay:
                time.sleep(delay)
        try:
            with httpx.Client(
                timeout=DEFAULT_TIMEOUT_SECONDS, transport=transport
            ) as client:
                response = client.post(resolved_url, json=payload)

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = f"HTTP {response.status_code}"
                logger.warning("Ryne attempt %d failed: %s", attempt + 1, last_error)
                continue
            if response.status_code >= 400:
                raise HumanizeError(
                    "Ryne AI rejected request: "
                    f"HTTP {response.status_code}{_extract_error_detail(response)}"
                )
            try:
                data = response.json()
            except ValueError:
                last_error = "invalid JSON response"
                logger.warning("Ryne attempt %d returned invalid JSON", attempt + 1)
                continue

            content = data.get("content")
            if not isinstance(content, str) or not content.strip():
                last_error = "response missing content"
                logger.warning("Ryne attempt %d returned no content", attempt + 1)
                continue
            return HumanizeResult(
                content=content, ai_score=_parse_ai_score(data.get("aiScore"))
            )
        except httpx.HTTPError as exc:
            last_error = str(exc)
            logger.warning("Ryne attempt %d network error: %s", attempt + 1, exc)

    attempts = len(backoff_seconds) + 1
    raise HumanizeError(f"Ryne AI failed after {attempts} attempts: {last_error}")


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("error_msg", "message", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return f" ({value})"
    status = data.get("status")
    if isinstance(status, str) and status and status.lower() != "error":
        return f" ({status})"
    return ""


def _parse_ai_score(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().rstrip("%")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None
