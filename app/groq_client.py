import json
import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.docx_engine import DocumentAnalysis, ParagraphInfo, resolve_protection

logger = logging.getLogger(__name__)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_CANDIDATE_TEXT_CHARS = 200

SYSTEM_PROMPT = (
    "You are a document-structure classifier for academic documents such as "
    "theses. You receive numbered paragraphs that might be section headings. "
    "Decide which are genuine section headings or titles (title page, "
    "declaration, certification, dedication, acknowledgements, table of "
    "contents, list of tables or figures, preface, abstract, chapter, "
    "appendix, bibliography, references) and which are ordinary sentences "
    "that only resemble headings. Respond with JSON only: "
    '{"boundaries": [list_of_numbers]}'
)


@dataclass(frozen=True)
class Candidate:
    index: int
    text: str


def collect_candidates(analysis: DocumentAnalysis) -> list[Candidate]:
    return [
        Candidate(index=info.index, text=info.text)
        for info in analysis.paragraphs
        if not info.is_protected and _is_heading_like_candidate(info)
    ]


def _is_heading_like_candidate(info: ParagraphInfo) -> bool:
    text = info.text.strip()
    if not text or len(text) > 80:
        return False
    if text.endswith((".", "?", "!", ",", ";")):
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(ch.isupper() for ch in letters) / len(letters)
    return upper_ratio >= 0.9


def classify_candidates(
    candidates: list[Candidate],
    *,
    api_key: str | None = None,
    model: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> set[int]:
    if not candidates:
        return set()

    settings = get_settings()
    resolved_key = api_key if api_key is not None else settings.groq_api_key
    resolved_model = model if model is not None else settings.groq_model

    try:
        content = _request_classification(
            candidates, resolved_key, resolved_model, transport
        )
        parsed = json.loads(content)
        raw_indices = parsed.get("boundaries", [])
        valid_indices = {candidate.index for candidate in candidates}
        confirmed = {int(i) for i in raw_indices if int(i) in valid_indices}
    except Exception:
        logger.exception("Groq classification failed; applying fail-safe protection")
        confirmed = {candidate.index for candidate in candidates}

    logger.info("Groq confirmed %d boundary candidate(s)", len(confirmed))
    return confirmed


def _request_classification(
    candidates: list[Candidate],
    api_key: str,
    model: str,
    transport: httpx.BaseTransport | None,
) -> str:
    listing = "\n".join(
        f"{candidate.index}. {candidate.text[:MAX_CANDIDATE_TEXT_CHARS]}"
        for candidate in candidates
    )
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": listing},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, transport=transport) as client:
        response = client.post(GROQ_CHAT_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    return data["choices"][0]["message"]["content"]


def apply_boundaries(analysis: DocumentAnalysis, boundary_indices: set[int]) -> None:
    for info in analysis.paragraphs:
        if info.index in boundary_indices and not info.is_protected:
            info.is_section_title = True
            info.classified_by = "groq"
    resolve_protection(analysis.paragraphs)
