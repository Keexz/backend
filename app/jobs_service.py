import io
import logging
from collections import defaultdict

from app.docx_engine import analyze_document, load_document
from app.docx_rewriter import replace_paragraph_sentences
from app.job_store import Job, JobStatus, ParagraphOutcome
from app.masking import mask_numbers_and_equations, unmask_text
from app.ryne_client import (
    DEFAULT_BACKOFF_SECONDS,
    HumanizeError,
    humanize_text,
)
from app.sentence import segment_sentences

logger = logging.getLogger(__name__)


def _groq_transport():  # kept for test compatibility, not used (Groq removed)
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

    # Build sentence-level worklist: only unprotected + highlighted sentences
    # Groq removed per request — protection is purely rule-based (Heading 1/2 + section titles),
    # then directly detect highlighted sentences and send to Ryne.
    candidate_sentences = [s for s in analysis.sentences if s.has_highlight and not s.is_protected]

    # Filter equation/number-only sentences (deterministic masking check)
    worklist: list = []
    for s in candidate_sentences:
        mask_probe = mask_numbers_and_equations(s.text)
        if mask_probe.is_equation_only:
            logger.info("Skipping equation-only sentence %d in paragraph %d", s.global_index, s.paragraph_index)
            continue
        worklist.append(s)

    job.total_paragraphs = len(worklist)
    job.total_sentences = len(worklist)

    ryne_result_transport = (
        ryne_transport if ryne_transport is not None else _ryne_transport()
    )

    # Map paragraph_index -> {global_index -> humanized_text}
    replacements_by_paragraph: dict[int, dict[int, str]] = defaultdict(dict)
    # Keep ai_score per global_index

    for completed_count, sentence in enumerate(worklist, start=1):
        job.status = JobStatus.HUMANIZING

        mask_result = mask_numbers_and_equations(sentence.text)
        # Already checked equation_only, but guard again
        if mask_result.is_equation_only:
            job.processed_paragraphs = completed_count
            job.processed_sentences = completed_count
            continue

        masked_text = mask_result.masked_text

        try:
            result = humanize_text(
                masked_text,
                transport=ryne_result_transport,
                backoff_seconds=_ryne_backoff(),
            )
        except HumanizeError as exc:
            logger.warning(
                "Sentence %d (para %d) could not be humanized: %s", sentence.global_index, sentence.paragraph_index, exc
            )
            job.failures.append(
                ParagraphOutcome(index=sentence.global_index, error=str(exc))
            )
        else:
            unmasked = unmask_text(result.content, mask_result.mapping)
            replacements_by_paragraph[sentence.paragraph_index][sentence.global_index] = unmasked
            job.succeeded.append(
                ParagraphOutcome(index=sentence.global_index, ai_score=result.ai_score)
            )

        job.processed_paragraphs = completed_count
        job.processed_sentences = completed_count

    # Rewrite paragraphs that have at least one successful replacement
    for para_idx, repl_map in replacements_by_paragraph.items():
        paragraph = document.paragraphs[para_idx]
        # Build ordered sentence list for this paragraph
        para_sentences = [s for s in analysis.sentences if s.paragraph_index == para_idx]
        if not para_sentences:
            continue
        # Ensure segmentation matches; use segment_sentences on current paragraph text
        spans = segment_sentences(paragraph.text)
        if len(spans) != len(para_sentences):
            # Fallback: spans mismatch due to prior mutation; use stored sentence texts
            new_texts = []
            for s in para_sentences:
                new_texts.append(repl_map.get(s.global_index, s.text))
            # Join with space fallback
            from app.docx_rewriter import replace_paragraph_text

            replace_paragraph_text(paragraph, " ".join(new_texts))
            continue
        new_texts = []
        for s in para_sentences:
            new_texts.append(repl_map.get(s.global_index, s.text))
        replace_paragraph_sentences(paragraph, new_texts)

    buffer = io.BytesIO()
    document.save(buffer)
    job.output_bytes = buffer.getvalue()

    if job.failures:
        job.status = JobStatus.COMPLETED_WITH_FAILURES
    else:
        job.status = JobStatus.COMPLETED
