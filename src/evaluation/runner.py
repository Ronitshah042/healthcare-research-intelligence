"""Run an evaluation question set and render the report and spot-check sheet."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from generation.cache import AnswerCache
from generation.llm import LLMClient, LLMRateLimitedError
from generation.rag import Answer, answer_question, is_usable_evidence
from ingestion.fulltext_store import FullTextStore, PassageMatch
from retrieval.embeddings import Embedder
from retrieval.hybrid import CANDIDATES, fuse, vector_search

from . import metrics
from .judge import ClaimVerdict, extract_claims, judge_answer

K_VALUES = (1, 3, 5)
METHODS = ("keyword", "semantic", "hybrid")
SPOTCHECK_CLAIMS = 15
_CITATION = re.compile(r"\[\d+\]")


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    question: str
    relevant_pmcids: tuple[str, ...]
    relevant_pmids: tuple[str, ...]
    should_abstain: bool
    needs_review: bool
    note: str


@dataclass
class QuestionResult:
    question: EvalQuestion
    relevant: set[str]
    rankings: dict[str, list[str]]
    answer: Answer | None = None
    latency_seconds: float | None = None
    verdicts: tuple[ClaimVerdict, ...] = ()


@dataclass
class EvalRun:
    started: datetime
    question_set: str
    database: str
    documents: int
    passages: int
    embedder_id: str | None
    answer_model: str | None
    judge_model: str | None
    llm_calls: int
    results: list[QuestionResult] = field(default_factory=list)
    # Why judging stopped early, if it did.
    judge_stopped: str | None = None
    # Answers not judged because the judge model wrote them.
    judge_skipped: int = 0


def load_question_set(path: Path) -> tuple[EvalQuestion, ...]:
    """Read and validate a question set file (JSON, ``"format": 1``)."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get("format") != 1:
        raise ValueError("The question set must be a JSON object with \"format\": 1.")
    questions, seen = [], set()
    for item in data.get("questions", []):
        try:
            entry = EvalQuestion(
                id=_text(item["id"]),
                question=_text(item["question"]),
                relevant_pmcids=tuple(_text(v).upper() for v in item.get("relevant_pmcids", [])),
                relevant_pmids=tuple(_text(v) for v in item.get("relevant_pmids", [])),
                should_abstain=_flag(item.get("should_abstain", False)),
                needs_review=_flag(item.get("needs_review", False)),
                note=str(item.get("note", "")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid question entry: {item!r:.80}") from error
        if entry.id in seen:
            raise ValueError(f"Duplicate question id {entry.id!r}.")
        if not entry.should_abstain and not (entry.relevant_pmcids or entry.relevant_pmids):
            raise ValueError(f"Question {entry.id} needs relevant articles or should_abstain.")
        seen.add(entry.id)
        questions.append(entry)
    if not questions:
        raise ValueError("The question set has no questions.")
    return tuple(questions)


def run_evaluation(
    questions: Sequence[EvalQuestion],
    *,
    store: FullTextStore,
    embedder: Embedder | None,
    answer_llm: LLMClient | None,
    judge_llm: LLMClient | None,
    cache: AnswerCache,
    judge_prompt: str,
    question_set: str,
    answer_cache=None,
    database: str,
    pace_seconds: float,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
    now: datetime,
) -> EvalRun:
    """Score retrieval for every question; answer and judge when an LLM is given.

    Waits ``pace_seconds`` after every question that used an AI request.
    """
    documents = store.documents()
    by_pmid = {doc.pmid: doc.pmcid for doc in documents if doc.pmid}
    stats = store.stats()
    calls_before = cache.requests_today()
    run = EvalRun(
        started=now, question_set=question_set, database=database,
        documents=stats.documents, passages=stats.chunks,
        embedder_id=embedder.model_id if embedder else None,
        answer_model=f"{answer_llm.provider} {answer_llm.model}" if answer_llm else None,
        judge_model=f"{judge_llm.provider} {judge_llm.model}" if judge_llm else None,
        llm_calls=0,
    )
    for entry in questions:
        relevant = set(entry.relevant_pmcids) | {
            by_pmid[pmid] for pmid in entry.relevant_pmids if pmid in by_pmid
        }
        result = QuestionResult(entry, relevant, _rankings(store, embedder, entry.question))
        if answer_llm is not None:
            calls = cache.requests_today()
            started = clock()
            result.answer = answer_question(
                entry.question, store=store, llm=answer_llm,
                cache=answer_cache or cache, embedder=embedder,
            )
            result.latency_seconds = clock() - started
            if judge_llm is not None and result.answer.model in judge_llm.model.split("+"):
                run.judge_skipped += 1  # a model never judges its own answers
            elif judge_llm is not None and run.judge_stopped is None:
                try:
                    result.verdicts = judge_answer(
                        result.answer, llm=judge_llm, cache=cache, prompt=judge_prompt,
                    )
                except LLMRateLimitedError as error:
                    # Free quota used up: further requests would only be refused.
                    run.judge_stopped = f"at {entry.id}: {error}"
            if cache.requests_today() > calls:
                sleeper(pace_seconds)
        run.results.append(result)
    run.llm_calls = cache.requests_today() - calls_before
    return run


def retrieval_table(run: EvalRun) -> dict[str, dict[str, float | None]]:
    """Mean recall@k, precision@k and MRR per method, over answerable questions."""
    scored = [r for r in run.results if r.relevant and not r.question.should_abstain]
    table = {}
    for method in METHODS:
        rows = [r for r in scored if method in r.rankings]
        if not rows:
            continue
        values: dict[str, float | None] = {"questions": len(rows)}
        for k in K_VALUES:
            values[f"recall@{k}"] = metrics.mean(
                metrics.recall_at_k(r.rankings[method], r.relevant, k) for r in rows)
            values[f"precision@{k}"] = metrics.mean(
                metrics.precision_at_k(r.rankings[method], r.relevant, k) for r in rows)
        values["MRR"] = metrics.mean(
            metrics.reciprocal_rank(r.rankings[method], r.relevant) for r in rows)
        table[method] = values
    return table


def answer_summary(run: EvalRun) -> dict[str, float | int | None]:
    """Citation validity, judge verdicts, abstention, latency, and AI calls."""
    answered = [r for r in run.results if r.answer is not None]
    judged = [r for r in answered if r.answer.abstain_reason != "busy"]
    valid = sum(len(_CITATION.findall(r.answer.text)) for r in judged if not r.answer.abstained)
    with_relevant = [r for r in judged if r.relevant and not r.question.should_abstain]
    evidence_recall = metrics.mean(
        len(r.relevant & {m.chunk.pmcid for m in r.answer.evidence}) / len(r.relevant)
        for r in with_relevant
    )
    invalid = sum(r.answer.invalid_citations for r in judged)
    verdicts = metrics.verdict_counts(v.verdict for r in judged for v in r.verdicts)
    rated = sum(verdicts.values())
    should = [r for r in judged if r.question.should_abstain]
    should_not = [r for r in judged if not r.question.should_abstain]
    # Only answers that actually waited for a model; early abstentions are instant.
    fresh = [r.latency_seconds for r in judged if r.latency_seconds is not None
             and r.answer.provider is not None and not r.answer.cached]
    return {
        "questions": len(answered),
        "busy": len(answered) - len(judged),
        "evidence_recall": evidence_recall,
        "citations_valid": valid,
        "citations_invalid": invalid,
        "citation_validity": metrics.citation_validity_rate(valid, invalid),
        "claims_judged": rated,
        "claims_unjudged": sum(len(r.verdicts) for r in judged) - rated,
        "supported": metrics.rate(verdicts["supported"], rated),
        "partial": metrics.rate(verdicts["partial"], rated),
        "unsupported": metrics.rate(verdicts["unsupported"], rated),
        "should_abstain": len(should),
        "correct_abstention": metrics.rate(sum(r.answer.abstained for r in should), len(should)),
        "should_answer": len(should_not),
        "answered_when_expected": metrics.rate(
            sum(not r.answer.abstained for r in should_not), len(should_not)),
        "latency_measured": len(fresh),
        "latency_median": metrics.percentile(fresh, 0.5),
        "latency_p90": metrics.percentile(fresh, 0.9),
        "llm_calls": run.llm_calls,
    }


def answer_models(run: EvalRun) -> dict[str, int]:
    """How many answers each model actually wrote (cached answers included)."""
    counts: dict[str, int] = {}
    for r in run.results:
        if r.answer is not None and r.answer.provider:
            name = f"{r.answer.provider} {r.answer.model}"
            counts[name] = counts.get(name, 0) + 1
    return counts


def run_summary(run: EvalRun, label: str) -> dict:
    """Headline numbers as JSON-ready data, for before/after comparisons."""
    return {
        "label": label,
        "date": f"{run.started:%Y-%m-%d %H:%M} UTC",
        "question_set": run.question_set,
        "questions": len(run.results),
        "answer_models": answer_models(run),
        "judge_model": run.judge_model,
        "retrieval": retrieval_table(run),
        "answers": answer_summary(run),
    }


# (label, section, key, format) rows of the before/after table.
COMPARISON_ROWS = (
    ("Hybrid recall@5", "retrieval", ("hybrid", "recall@5"), "pct"),
    ("Hybrid MRR", "retrieval", ("hybrid", "MRR"), "num"),
    ("Evidence recall (relevant articles sent to the model)", "answers", ("evidence_recall",), "pct"),
    ("Citation validity", "answers", ("citation_validity",), "pct"),
    ("Claims supported (judge)", "answers", ("supported",), "pct"),
    ("Claims unsupported (judge)", "answers", ("unsupported",), "pct"),
    ("Correct abstention", "answers", ("correct_abstention",), "pct"),
    ("Answered when expected", "answers", ("answered_when_expected",), "pct"),
    ("AI requests", "answers", ("llm_calls",), "int"),
)


def comparison_table(before: dict, after: dict) -> list[str]:
    """Markdown rows comparing two run summaries."""
    def value(summary: dict, section: str, keys: tuple[str, ...]):
        node = summary.get(section, {})
        for key in keys:
            node = node.get(key) if isinstance(node, dict) else None
        return node

    show = {"pct": _pct, "num": _num, "int": lambda v: "n/a" if v is None else str(v)}
    lines = [_row(["Measure", before.get("label", "before"), after.get("label", "after"),
                   "Change"]), _row(["---"] * 4)]
    for name, section, keys, kind in COMPARISON_ROWS:
        old, new = value(before, section, keys), value(after, section, keys)
        if old is None or new is None:
            change = "n/a"
        elif kind == "pct":
            change = f"{(new - old) * 100:+.0f} points"
        else:
            change = f"{new - old:+.2f}" if kind == "num" else f"{new - old:+d}"
        lines.append(_row([name, show[kind](old), show[kind](new), change]))
    return lines


def render_report(
    run: EvalRun,
    weaknesses: Sequence[str],
    *,
    current: dict | None = None,
    baseline: dict | None = None,
    note: str | None = None,
    agreement: dict | None = None,
    latency_record: dict | None = None,
) -> str:
    """The Markdown report for ``docs/EVAL_RESULTS.md``."""
    retrieval = retrieval_table(run)
    answers = answer_summary(run)
    reviewed = sum(r.question.needs_review for r in run.results)
    written = answer_models(run)
    lines = [
        "# Evaluation results"
        + (": preliminary (questions under review)" if reviewed else ""),
        "",
        *([f"**Preliminary:** {reviewed} questions are still marked `needs_review`; "
           "the numbers may change once they are checked.", ""] if reviewed else []),
        f"Run {run.started:%Y-%m-%d %H:%M} UTC with `{run.question_set}` "
        f"({len(run.results)} questions, {reviewed} still marked `needs_review`) "
        f"on `{run.database}`: {run.documents} articles, {run.passages} passages.",
        f"Embedding model: {run.embedder_id or 'none (keyword only)'}. "
        "Answers written by: " + (", ".join(
            f"{name} ({count})" for name, count in written.items()) or "none") + ". "
        f"Judge model: {run.judge_model or 'not run'}"
        + (f" ({run.judge_skipped} answers by the judge model itself not judged)"
           if run.judge_skipped else "") + ". "
        f"AI requests used: {run.llm_calls}.",
        *([f"**Judging stopped {run.judge_stopped}** Later answers have no verdicts; "
           "rerun to continue (answers and verdicts are cached)."]
          if run.judge_stopped else []),
        "",
        "Generated by `hri eval`. Numbers come from a small starter set; treat "
        "differences of a few points as noise.",
        "",
        "## Retrieval (per article, answerable questions)",
        "",
    ]
    if retrieval:
        header = ["Method", "Questions", *(f"Recall@{k}" for k in K_VALUES),
                  *(f"Precision@{k}" for k in K_VALUES), "MRR"]
        lines += [_row(header), _row(["---"] * len(header))]
        for method, values in retrieval.items():
            lines.append(_row([method, str(values["questions"]),
                               *(_pct(values[f"recall@{k}"]) for k in K_VALUES),
                               *(_pct(values[f"precision@{k}"]) for k in K_VALUES),
                               _num(values["MRR"])]))
    else:
        lines.append("No answerable questions with known relevant articles.")
    lines += ["", "## Answers", ""]
    if answers["questions"]:
        rows = [
            ("Questions answered or abstained", str(answers["questions"])),
            ("Busy (no AI available, excluded below)", str(answers["busy"])),
            ("Evidence recall (relevant articles sent to the model)",
             _pct(answers["evidence_recall"])),
            ("Citation validity", f"{_pct(answers['citation_validity'])} "
             f"({answers['citations_valid']} valid, {answers['citations_invalid']} removed)"),
            ("Claims rated by the judge", f"{answers['claims_judged']} "
             f"(+{answers['claims_unjudged']} unrated)"),
            ("Supported / partial / unsupported", f"{_pct(answers['supported'])} / "
             f"{_pct(answers['partial'])} / {_pct(answers['unsupported'])}"),
            ("Judge agreement", _agreement(agreement)),
            ("Correct abstention", f"{_pct(answers['correct_abstention'])} "
             f"of {answers['should_abstain']} that should abstain"),
            ("Answered when expected", f"{_pct(answers['answered_when_expected'])} "
             f"of {answers['should_answer']} answerable"),
            ("Response time, uncached", _latency(answers, latency_record, run)),
            ("AI requests (answers + judge)", str(answers["llm_calls"])),
        ]
        lines += [_row(["Measure", "Value"]), _row(["---", "---"])]
        lines += [_row(list(row)) for row in rows]
    else:
        lines.append("Answers were not run (`--no-answers`).")
    lines += ["", "## Per question", "",
              _row(["Id", "Expect", "Hybrid R@5", "Hybrid RR", "Outcome", "Citations",
                    "Judge (S/P/U)", "Review"]),
              _row(["---"] * 8)]
    for r in run.results:
        method = "hybrid" if "hybrid" in r.rankings else "keyword"
        scored = r.relevant and not r.question.should_abstain
        verdicts = metrics.verdict_counts(v.verdict for v in r.verdicts)
        outcome = "-" if r.answer is None else (
            f"abstained ({r.answer.abstain_reason})" if r.answer.abstained else "answered")
        citations = "-" if r.answer is None else (
            f"{len(_CITATION.findall(r.answer.text)) if not r.answer.abstained else 0}"
            f" ok / {r.answer.invalid_citations} removed")
        lines.append(_row([
            r.question.id,
            "abstain" if r.question.should_abstain else "answer",
            _pct(metrics.recall_at_k(r.rankings[method], r.relevant, 5)) if scored else "-",
            _num(metrics.reciprocal_rank(r.rankings[method], r.relevant)) if scored else "-",
            outcome, citations,
            f"{verdicts['supported']}/{verdicts['partial']}/{verdicts['unsupported']}"
            if r.verdicts else "-",
            "yes" if r.question.needs_review else "",
        ]))
    if baseline is not None and current is not None:
        lines += ["", "## Before and after", "",
                  f"`{baseline.get('label')}` ({baseline.get('date')}) against "
                  f"`{current.get('label')}` ({current.get('date')}), same question set.", ""]
        lines += comparison_table(baseline, current)
        if note:
            lines += ["", f"**Decision:** {note}"]
    lines += ["", "## Interpretation", ""] + [f"- {line}" for line in _interpret(retrieval, answers)]
    lines += ["", "## Known weaknesses", ""] + [f"- {line}" for line in weaknesses]
    return "\n".join(lines) + "\n"


def spotcheck_csv(run: EvalRun, limit: int = SPOTCHECK_CLAIMS) -> str:
    """A sheet of cited claims for the owner to verify by hand.

    Claims the judge did not rate are included with verdict "unjudged".
    """
    owners = [r for r in run.results if r.answer is not None and not r.answer.abstained]
    queues = [
        list(r.verdicts) or [ClaimVerdict(claim, cited, "unjudged", "")
                             for claim, cited in extract_claims(r.answer.text)]
        for r in owners
    ]
    picked: list[tuple[QuestionResult, ClaimVerdict]] = []
    while len(picked) < limit and any(queues):
        for owner, queue in zip(owners, queues):  # one claim per question per round
            if queue and len(picked) < limit:
                picked.append((owner, queue.pop(0)))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["question_id", "question", "claim", "cited", "cited_articles",
                     "cited_passage_ids", "cited_passages", "judge_verdict",
                     "judge_reason", "your_verdict", "your_notes"])
    for result, verdict in picked:
        evidence = result.answer.evidence
        cited = [evidence[n - 1] for n in verdict.cited if 1 <= n <= len(evidence)]
        writer.writerow([
            result.question.id, result.question.question, verdict.claim,
            " ".join(f"[{n}]" for n in verdict.cited),
            " ".join(f"{m.chunk.pmcid_version}/{m.chunk.section_name}" for m in cited),
            " ".join(passage_id(m) for m in cited),
            "\n---\n".join(m.chunk.text[:1500] for m in cited),
            verdict.verdict, verdict.reason, "", "",
        ])
    return out.getvalue()


def passage_id(match: PassageMatch) -> str:
    """Stable passage reference: article version and position, e.g. PMC1.1:p4."""
    return f"{match.chunk.pmcid_version}:p{match.chunk.position}"


def _rankings(
    store: FullTextStore, embedder: Embedder | None, question: str
) -> dict[str, list[str]]:
    def articles(matches: tuple[PassageMatch, ...]) -> list[str]:
        return metrics.article_ranking(
            m.chunk.pmcid for m in matches if is_usable_evidence(m))

    try:
        keyword = store.search(question, limit=CANDIDATES, match_all=False)
    except ValueError:
        keyword = ()
    rankings = {"keyword": articles(keyword)}
    if embedder is not None:
        semantic = vector_search(store, embedder, question, limit=CANDIDATES)
        rankings["semantic"] = articles(semantic)
        rankings["hybrid"] = articles(fuse(keyword, semantic, limit=CANDIDATES))
    return rankings


def _interpret(retrieval: dict, answers: dict) -> list[str]:
    lines = []
    if retrieval:
        best = max(retrieval, key=lambda m: retrieval[m]["MRR"] or 0)
        lines.append(
            f"Best retrieval method by MRR: {best} ({_num(retrieval[best]['MRR'])}); "
            "recall@5 by method: " + ", ".join(
                f"{m} {_pct(v['recall@5'])}" for m, v in retrieval.items()) + ".")
    if answers.get("questions"):
        lines.append(
            f"{_pct(answers['citation_validity'])} of citations named a supplied passage; "
            f"the judge rated {_pct(answers['supported'])} of cited claims fully "
            f"supported and {_pct(answers['unsupported'])} unsupported.")
        lines.append(
            f"The system abstained on {_pct(answers['correct_abstention'])} of questions it "
            f"should refuse and answered {_pct(answers['answered_when_expected'])} of "
            "answerable ones.")
        lines.append("The judge is a different AI model from the one that wrote the "
                     "answers, but still an AI; the spot-check sheet is the human check "
                     "of its verdicts.")
    return lines or ["No results."]


def _agreement(agreement: dict | None) -> str:
    if not agreement or not agreement.get("rated"):
        return "pending manual spot-check"
    return (f"{_pct(agreement['rate'])} ({agreement['agreed']} of {agreement['rated']} "
            "claims; exact verdict match; spot-check reviewed by the owner with "
            "AI-assisted notes)")


def _latency(answers: dict, record: dict | None, run: EvalRun) -> str:
    measured = answers.get("latency_measured") or 0
    if record:
        # The record is this run's own sample unless the sample was too small to replace it.
        own = measured and (record["date"], record["answers_measured"], record["median_seconds"]) == (
            f"{run.started:%Y-%m-%d}", measured, round(answers["latency_median"], 2))
        suffix = ("" if own else
                  f"; this run's {measured} uncached answers are logged in latency-samples"
                  if measured else "; this run was fully cached")
        return (f"median {record['median_seconds']:.1f} s, p90 {record['p90_seconds']:.1f} s "
                f"(uncached run, {record['date']}, {record['answers_measured']} answers{suffix})")
    if answers.get("latency_median") is not None:
        return (f"median {_secs(answers['latency_median'])}, p90 {_secs(answers['latency_p90'])} "
                f"(uncached run, {run.started:%Y-%m-%d}, {answers['latency_measured']} answers)")
    return "not measured yet (run with --no-cache)"


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _secs(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} s"


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected non-empty text")
    return value.strip()


def _flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("expected true or false")
    return value
