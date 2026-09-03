# This file is the main worker for a humanizer job.
# In simple terms: it loads the .docx, finds sentences covered by * markers,
# checks CPU, sends each marked sentence to Ryne AI, then writes it back.

import io
import logging
import time
from collections import defaultdict

from app.docx_engine import (
    analyze_document,
    load_document,
    strip_candidate_marker_characters,
)
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


# Check CPU only periodically so a 649-sentence job does not spend ~65s
# just measuring CPU. Render free has 0.1 CPU, so one spike should not abort.
CPU_CHECK_EVERY_N_SENTENCES = 10
CPU_OVERLOAD_THRESHOLD = 80.0
CPU_CONFIRM_ATTEMPTS = 3
CPU_CONFIRM_DELAY_SECONDS = 1.0


def _should_check_cpu(completed_count: int) -> bool:
    # Check the first sentence, then every N sentences.
    return (completed_count - 1) % CPU_CHECK_EVERY_N_SENTENCES == 0


def _is_cpu_overloaded() -> bool:
    # Non-blocking first read adds no load; confirm spikes twice before stopping.
    try:
        import psutil  # type: ignore

        for attempt in range(CPU_CONFIRM_ATTEMPTS):
            if attempt == 0:
                cpu = psutil.cpu_percent(interval=None)
            else:
                time.sleep(CPU_CONFIRM_DELAY_SECONDS)
                cpu = psutil.cpu_percent(interval=0.1)
            if cpu <= CPU_OVERLOAD_THRESHOLD:
                return False
            logger.warning(
                "CPU %.1f%% is above the %.0f%% job limit (check %d/%d)",
                cpu,
                CPU_OVERLOAD_THRESHOLD,
                attempt + 1,
                CPU_CONFIRM_ATTEMPTS,
            )
        return True
    except ImportError:
        logger.debug("psutil not installed — skipping CPU guard")
    except Exception:
        logger.exception("CPU check failed — continuing without the guard")
    return False


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

    # Build the worklist from unprotected sentences overlapping valid markers.
    # Groq removed per Interview Round 4 — protection is purely rule-based.
    candidate_sentences = [
        sentence
        for sentence in analysis.sentences
        if sentence.has_highlight and not sentence.is_protected
    ]

    # Skip a sentence only when it contains no natural language after masking.
    worklist: list = []
    for sentence in candidate_sentences:
        stripped = strip_candidate_marker_characters(sentence.text)
        mask_probe = mask_numbers_and_equations(stripped)
        if mask_probe.is_equation_only:
            logger.info(
                "Skipping equation-only sentence %d in paragraph %d",
                sentence.global_index,
                sentence.paragraph_index,
            )
            continue
        worklist.append(sentence)

    job.total_paragraphs = len(worklist)
    job.total_sentences = len(worklist)

    ryne_result_transport = (
        ryne_transport if ryne_transport is not None else _ryne_transport()
    )

    replacements_by_paragraph: dict[int, dict[int, str]] = defaultdict(dict)
    stopped_for_cpu = False

    try:
        import psutil  # type: ignore

        psutil.cpu_percent(interval=None)
    except Exception:
        pass

    for completed_count, sentence in enumerate(worklist, start=1):
        job.status = JobStatus.HUMANIZING

        # Throttled guard: only measure every N sentences, and only stop
        # after repeated high readings inside _is_cpu_overloaded().
        if _should_check_cpu(completed_count) and _is_cpu_overloaded():
            stopped_for_cpu = True
            break

        # Remove marker characters before sending this sentence to Ryne.
        stripped = strip_candidate_marker_characters(sentence.text)
        mask_result = mask_numbers_and_equations(stripped)
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
                "Sentence %d in paragraph %d could not be humanized: %s",
                sentence.global_index,
                sentence.paragraph_index,
                exc,
            )
            job.failures.append(
                ParagraphOutcome(index=sentence.global_index, error=str(exc))
            )
        else:
            unmasked = unmask_text(result.content, mask_result.mapping)
            replacements_by_paragraph[sentence.paragraph_index][
                sentence.global_index
            ] = unmasked
            job.succeeded.append(
                ParagraphOutcome(index=sentence.global_index, ai_score=result.ai_score)
            )

        job.processed_paragraphs = completed_count
        job.processed_sentences = completed_count

    if stopped_for_cpu:
        successful_indexes = {outcome.index for outcome in job.succeeded}
        for sentence in worklist:
            if sentence.global_index in successful_indexes:
                continue
            # Give every unfinished sentence its own valid marker pair for retry.
            retry_text = strip_candidate_marker_characters(sentence.text)
            replacements_by_paragraph[sentence.paragraph_index][
                sentence.global_index
            ] = f"*{retry_text}*"

    # Rebuild paragraphs containing successful text or normalized retry markers.
    for paragraph_index, replacements in replacements_by_paragraph.items():
        paragraph = document.paragraphs[paragraph_index]
        paragraph_sentences = [
            sentence
            for sentence in analysis.sentences
            if sentence.paragraph_index == paragraph_index
        ]
        if not paragraph_sentences:
            continue

        spans = segment_sentences(paragraph.text)
        new_texts = [
            replacements.get(sentence.global_index, sentence.text)
            for sentence in paragraph_sentences
        ]
        if len(spans) != len(paragraph_sentences):
            from app.docx_rewriter import replace_paragraph_text

            replace_paragraph_text(paragraph, " ".join(new_texts))
            continue
        replace_paragraph_sentences(paragraph, new_texts)

    buffer = io.BytesIO()
    document.save(buffer)
    job.output_bytes = buffer.getvalue()

    if stopped_for_cpu:
        job.status = JobStatus.PARTIALLY_COMPLETED
        job.error = (
            "Server CPU usage rose above 80%. Remaining marked sentences were "
            "left unchanged and wrapped in asterisk markers for retry."
        )
    elif job.failures:
        job.status = JobStatus.COMPLETED_WITH_FAILURES
    else:
        job.status = JobStatus.COMPLETED
