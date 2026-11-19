"""UI-agnostic question answering over stored open-access passages.

Re-exported by ``hri.services``; the contract is in ``docs/SERVICE_API.md``.
An unavailable AI provider never raises: the ``Answer`` says so instead.
"""

from __future__ import annotations

import os
from pathlib import Path

from generation.cache import DEFAULT_DAILY_REQUEST_CAP, AnswerCache
from generation.llm import FallbackLLM, LLMClient, build_default_llm
from generation.rag import DEFAULT_EVIDENCE, DISCLAIMER, Answer, Citation, answer_question
from ingestion.fulltext_store import FullTextStore
from retrieval.embeddings import Embedder

from .config import resolve_database_path
from .errors import InvalidRequestError
from .retrieval_services import loaded_embedder


MAX_QUESTION_LENGTH = 1000
MAX_EVIDENCE = 10

__all__ = [
    "Answer",
    "Citation",
    "DISCLAIMER",
    "DEFAULT_EVIDENCE",
    "MAX_EVIDENCE",
    "MAX_QUESTION_LENGTH",
    "ask_question",
]


def ask_question(
    question: str,
    *,
    evidence_count: int = DEFAULT_EVIDENCE,
    database_path: str | Path | None = None,
    llm: LLMClient | None = None,
    embedder: Embedder | None = None,
) -> Answer:
    """Answer ``question`` from stored passages, citing every claim.

    Sends the question and up to ``evidence_count`` CC0/CC BY passages to the
    configured AI provider, unless a cached answer exists. Abstains, rather
    than guessing, when evidence or citations are insufficient.
    """
    if not isinstance(question, str) or not question.strip():
        raise InvalidRequestError("question must be non-empty text.")
    if len(question) > MAX_QUESTION_LENGTH:
        raise InvalidRequestError(
            f"question must be at most {MAX_QUESTION_LENGTH} characters."
        )
    if isinstance(evidence_count, bool) or not isinstance(evidence_count, int) or not (
        2 <= evidence_count <= MAX_EVIDENCE
    ):
        raise InvalidRequestError(f"evidence_count must be between 2 and {MAX_EVIDENCE}.")
    path = resolve_database_path(database_path)
    store = FullTextStore(path)
    cache = AnswerCache(path, daily_cap=_daily_cap())
    if llm is None:
        client = build_default_llm(before_request=cache.count_request)
    else:
        client = FallbackLLM([llm], before_request=cache.count_request)
    if embedder is None:
        embedder, _ = loaded_embedder()
    return answer_question(
        question.strip(), store=store, llm=client, cache=cache,
        embedder=embedder, evidence_count=evidence_count,
    )


def _daily_cap() -> int:
    value = os.environ.get("HRI_LLM_DAILY_REQUEST_CAP", "").strip()
    if not value:
        return DEFAULT_DAILY_REQUEST_CAP
    if not (value.isascii() and value.isdigit()):
        raise InvalidRequestError(
            "HRI_LLM_DAILY_REQUEST_CAP must be a whole number of 0 or more."
        )
    return int(value)
