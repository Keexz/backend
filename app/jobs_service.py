import io
import logging

from app.docx_engine import analyze_document, load_document
from app.docx_rewriter import replace_paragraph_text
from app.groq_client import apply_boundaries, classify_candidates, collect_candidates
from app.job_store import Job, JobStatus, ParagraphOutcome
from app.ryne_client import (
    DEFAULT_BACKOFF_SECONDS,
    HumanizeError,
    humanize_text,
)

logger = logging.getLogger(__name__)


def _groq_transport():
    return None


def _ryne_transport():
    return None


def _ryne_backoff():
    return DEFAULT_BACKOFF_SECONDS


def process_job(
    job: Job,
    file_bytes: bytes,
    *,
    ryne_transport=None,
    groq_transport=None,
) -> None:
    job.status = JobStatus.ANALYZING

    try:
        document = load_document(file_bytes)
    except Exception:
        logger.exception("Failed to parse uploaded document")
        job.status = JobStatus.FAILED
        job.error = "Uploaded file is not a valid .docx document."
        return

    analysis = analyze_document(document)

    groq_result_transport = (
        groq_transport if groq_transport is not None else _groq_transport()
    )
    candidates = collect_candidates(analysis)
    if candidates:
        boundaries = classify_candidates(candidates, transport=groq_result_transport)
        apply_boundaries(analysis, boundaries)

    worklist = analysis.humanizable_indices
    job.total_paragraphs = len(worklist)

    ryne_result_transport = (
        ryne_transport if ryne_transport is not None else _ryne_transport()
    )

    for completed_count, paragraph_index in enumerate(worklist, start=1):
        job.status = JobStatus.HUMANIZING
        info = analysis.paragraphs[paragraph_index]
        paragraph = document.paragraphs[paragraph_index]

        try:
            result = humanize_text(
                info.text,
                transport=ryne_result_transport,
                backoff_seconds=_ryne_backoff(),
            )
        except HumanizeError as exc:
            logger.warning(
                "Paragraph %d could not be humanized: %s", paragraph_index, exc
            )
            job.failures.append(
                ParagraphOutcome(index=paragraph_index, error=str(exc))
            )
        else:
            replace_paragraph_text(paragraph, result.content)
            job.succeeded.append(
                ParagraphOutcome(index=paragraph_index, ai_score=result.ai_score)
            )

        job.processed_paragraphs = completed_count

    buffer = io.BytesIO()
    document.save(buffer)
    job.output_bytes = buffer.getvalue()

    if job.failures:
        job.status = JobStatus.COMPLETED_WITH_FAILURES
    else:
        job.status = JobStatus.COMPLETED
