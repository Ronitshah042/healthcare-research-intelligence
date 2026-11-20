"""UI-agnostic evaluation runs. Re-exported by ``hri.services``.

The contract is in ``docs/SERVICE_API.md``.
"""

from __future__ import annotations

import csv
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from evaluation.runner import (
    answer_summary,
    load_question_set,
    render_report,
    retrieval_table,
    run_evaluation,
    run_summary,
    spotcheck_csv,
)
from evaluation.verdicts import export_verdicts as _export_verdicts
from evaluation.verdicts import judge_agreement
from generation.cache import AnswerCache
from generation.llm import FallbackLLM, GroqClient, LLMClient, build_default_llm
from ingestion.fulltext_store import FullTextStore
from retrieval.embeddings import Embedder

from .answer_services import daily_request_cap
from .config import resolve_database_path
from .errors import InvalidRequestError
from .retrieval_services import loaded_embedder

# On Groq, so the judge is never the Gemini model that writes the answers.
# A different family from both answer models (Gemini, and Qwen on Groq).
DEFAULT_JUDGE_MODEL = "openai/gpt-oss-120b"
# Verdicts for many claims, plus any model reasoning, need more than answers do.
JUDGE_MAX_OUTPUT_TOKENS = 4096
DEFAULT_PACE_SECONDS = 5.0
DEFAULT_REPORT = Path("docs") / "EVAL_RESULTS.md"
DEFAULT_JUDGE_PROMPT = Path("eval") / "judge_prompt.txt"
# --no-cache spends real requests: a small default and a hard limit.
DEFAULT_NO_CACHE_QUESTIONS = 5
MAX_NO_CACHE_QUESTIONS = 10
KNOWN_WEAKNESSES = (
    "The starter questions were written by the developer from the stored "
    "abstracts, so their wording favours retrieval; real users' questions will "
    "score lower.",
    "Relevant articles were labelled by the developer; entries marked "
    "`needs_review` are unconfirmed.",
    "The judge (Groq) is a different model from the answer model (Gemini), but "
    "AI judges can still be lenient; the spot-check sheet measures its agreement "
    "with a human.",
    "Evidence is chosen per passage: one article can fill most of the passages "
    "sent to the model, crowding out other relevant articles.",
    "Passages longer than the embedding model's 512-token limit are cut off "
    "before embedding (`hri embed-passages` reports how many).",
    "Citation validity undercounts valid citations in answers withheld for "
    "having too many invented ones (their text is replaced).",
    "Retrieval is scored per article; a relevant article retrieved through an "
    "unhelpful passage still counts as found.",
)

__all__ = [
    "DEFAULT_PACE_SECONDS", "DEFAULT_REPORT", "MAX_NO_CACHE_QUESTIONS",
    "EvalResult", "run_eval", "export_verdicts",
]


@dataclass(frozen=True)
class EvalResult:
    """Where the outputs went, and the headline numbers."""

    report_path: Path
    spotcheck_path: Path | None
    questions: int
    llm_calls: int
    retrieval: dict
    answers: dict


def run_eval(
    question_set: str | Path,
    *,
    database_path: str | Path | None = None,
    max_questions: int | None = None,
    answers: bool = True,
    report_path: str | Path = DEFAULT_REPORT,
    spotcheck_dir: str | Path | None = None,
    pace_seconds: float = DEFAULT_PACE_SECONDS,
    judge_prompt_path: str | Path = DEFAULT_JUDGE_PROMPT,
    label: str = "run",
    summary_path: str | Path | None = None,
    baseline_path: str | Path | None = None,
    note: str | None = None,
    no_cache: bool = False,
    answer_llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
    embedder: Embedder | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: datetime | None = None,
) -> EvalResult:
    """Run a question set, write the Markdown report and the spot-check CSV.

    ``no_cache`` ignores cached answers (to measure real response time); it
    applies to at most ``MAX_NO_CACHE_QUESTIONS`` questions, 5 by default.
    """
    path = Path(question_set)
    try:
        questions = load_question_set(path)
    except (OSError, ValueError) as error:
        raise InvalidRequestError(f"Cannot use question set {path}: {error}") from None
    if no_cache and max_questions is None:
        max_questions = DEFAULT_NO_CACHE_QUESTIONS
    if max_questions is not None:
        if isinstance(max_questions, bool) or not isinstance(max_questions, int) or max_questions < 1:
            raise InvalidRequestError("max_questions must be a positive whole number.")
        if no_cache and max_questions > MAX_NO_CACHE_QUESTIONS:
            raise InvalidRequestError(
                f"With no_cache, max_questions can be at most {MAX_NO_CACHE_QUESTIONS} "
                "to protect free-tier limits."
            )
        questions = questions[:max_questions]
    if not (isinstance(pace_seconds, (int, float)) and 0 <= pace_seconds <= 120):
        raise InvalidRequestError("pace_seconds must be between 0 and 120.")
    database = resolve_database_path(database_path)
    store = FullTextStore(database)
    cache = AnswerCache(database, daily_cap=daily_request_cap())
    if embedder is None:
        embedder, _ = loaded_embedder()
    answer_client = judge_client = None
    if answers:
        answer_client = FallbackLLM(
            [answer_llm] if answer_llm else build_default_llm().clients,
            before_request=cache.count_request,
        )
        if judge_llm is None:
            groq_key = os.environ.get("GROQ_API_KEY", "").strip()
            model = os.environ.get("HRI_JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_MODEL
            judge_clients = (
                [GroqClient(groq_key, model, max_output_tokens=JUDGE_MAX_OUTPUT_TOKENS)]
                if groq_key else []
            )
        else:
            judge_clients = (judge_llm,)
        if judge_clients:
            judge_client = FallbackLLM(judge_clients, before_request=cache.count_request)
    run = run_evaluation(
        questions, store=store, embedder=embedder, answer_llm=answer_client,
        judge_llm=judge_client, cache=cache,
        answer_cache=_NoReuse(cache) if no_cache else None,
        judge_prompt=Path(judge_prompt_path).read_text() if answers else "",
        question_set=str(path), database=str(database), pace_seconds=pace_seconds,
        clock=clock, sleeper=sleeper, now=now or datetime.now(timezone.utc),
    )
    baseline = None
    if baseline_path is not None:
        try:
            baseline = json.loads(Path(baseline_path).read_text())
        except (OSError, ValueError) as error:
            raise InvalidRequestError(f"Cannot read baseline {baseline_path}: {error}") from None
    summary = run_summary(run, label)
    if summary_path is not None:
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
        Path(summary_path).write_text(json.dumps(summary, indent=2) + "\n")
    report = Path(report_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    results_dir = path.parent / "results"
    latency_path = results_dir / "latency.json"
    if summary["answers"].get("latency_median") is not None:
        results_dir.mkdir(parents=True, exist_ok=True)
        latency_path.write_text(json.dumps({
            "median_seconds": round(summary["answers"]["latency_median"], 2),
            "p90_seconds": round(summary["answers"]["latency_p90"], 2),
            "answers_measured": summary["answers"]["latency_measured"],
            "date": f"{run.started:%Y-%m-%d}",
            "answer_models": summary["answer_models"],
        }, indent=2) + "\n")
    latency_record = _read_json(latency_path)
    agreement = judge_agreement(
        p.read_text() for p in sorted(path.parent.glob("verdicts-*.csv")))
    report.write_text(render_report(
        run, KNOWN_WEAKNESSES, current=summary, baseline=baseline, note=note,
        agreement=agreement, latency_record=latency_record))
    spotcheck = None
    if answers:
        spotcheck = _free_spotcheck_path(
            Path(spotcheck_dir or path.parent), f"spotcheck-{run.started:%Y-%m-%d}")
        spotcheck.write_text(spotcheck_csv(run))
    return EvalResult(
        report_path=report, spotcheck_path=spotcheck, questions=len(run.results),
        llm_calls=run.llm_calls, retrieval=retrieval_table(run), answers=answer_summary(run),
    )


def export_verdicts(sheet_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Write the verdict-only copy of a spot-check sheet; returns its path.

    The default output is ``verdicts-<date>.csv`` beside the sheet, without
    any article text, so it can be committed.
    """
    sheet = Path(sheet_path)
    try:
        text = _export_verdicts(sheet.read_text())
    except (OSError, ValueError, csv.Error) as error:
        raise InvalidRequestError(f"Cannot export {sheet}: {error}") from None
    output = Path(output_path) if output_path else sheet.with_name(
        sheet.name.replace("spotcheck-", "verdicts-", 1))
    if output.resolve() == sheet.resolve():
        raise InvalidRequestError("The export must not overwrite the spot-check sheet.")
    output.write_text(text)
    return output


class _NoReuse:
    """Answer cache that stores replies but never returns one."""

    def __init__(self, cache: AnswerCache) -> None:
        self._cache = cache

    def get(self, key: str):
        return None

    def put(self, key: str, reply) -> None:
        self._cache.put(key, reply)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _free_spotcheck_path(directory: Path, stem: str) -> Path:
    """The day's sheet, unless a human has started filling it in: then a new name."""
    candidate, number = directory / f"{stem}.csv", 1
    while candidate.exists() and _has_human_input(candidate):
        number += 1
        candidate = directory / f"{stem}-{number}.csv"
    return candidate


def _has_human_input(path: Path) -> bool:
    try:
        with path.open(newline="") as handle:
            return any(
                (row.get("your_verdict") or "").strip() or (row.get("your_notes") or "").strip()
                for row in csv.DictReader(handle)
            )
    except (OSError, csv.Error, UnicodeDecodeError):
        return True  # unreadable: never overwrite it
