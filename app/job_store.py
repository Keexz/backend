import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    HUMANIZING = "humanizing"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    FAILED = "failed"


@dataclass
class ParagraphOutcome:
    index: int
    ai_score: float | None = None
    error: str | None = None


# Sentence-level alias for the sentence processing pipeline.
SentenceOutcome = ParagraphOutcome


@dataclass
class Job:
    id: str
    filename: str
    status: JobStatus = JobStatus.QUEUED
    total_paragraphs: int = 0
    processed_paragraphs: int = 0
    succeeded: list[ParagraphOutcome] = field(default_factory=list)
    failures: list[ParagraphOutcome] = field(default_factory=list)
    error: str | None = None
    output_bytes: bytes | None = None

    @property
    def total_sentences(self) -> int:
        return self.total_paragraphs

    @total_sentences.setter
    def total_sentences(self, value: int) -> None:
        self.total_paragraphs = value

    @property
    def processed_sentences(self) -> int:
        return self.processed_paragraphs

    @processed_sentences.setter
    def processed_sentences(self, value: int) -> None:
        self.processed_paragraphs = value

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "filename": self.filename,
            "status": self.status.value,
            "total_paragraphs": self.total_paragraphs,
            "total_sentences": self.total_paragraphs,
            "processed_paragraphs": self.processed_paragraphs,
            "processed_sentences": self.processed_paragraphs,
            "ai_scores": [
                {"index": o.index, "ai_score": o.ai_score} for o in self.succeeded
            ],
            "failures": [
                {"index": o.index, "error": o.error} for o in self.failures
            ],
            "error": self.error,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, filename: str) -> Job:
        job = Job(id=uuid.uuid4().hex, filename=filename)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)


job_store = JobStore()
