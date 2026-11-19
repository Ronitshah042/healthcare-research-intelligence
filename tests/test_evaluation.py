"""Tests for evaluation metrics, the judge, and the runner, with fixed fake data."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest

from evaluation import metrics
from evaluation.judge import build_judge_input, extract_claims, judge_answer, parse_verdicts
from evaluation.runner import load_question_set
from generation import rag
from generation.cache import AnswerCache
from hri import cli, services

from test_answer import FakeLLM, PipelineTestCase


class MetricTests(unittest.TestCase):
    ranking = ["A", "B", "C", "D", "E"]

    def test_article_ranking_keeps_first_appearance(self) -> None:
        self.assertEqual(metrics.article_ranking(["B", "A", "B", "C", "A"]), ["B", "A", "C"])

    def test_recall_precision_and_reciprocal_rank(self) -> None:
        relevant = {"B", "E", "Z"}
        self.assertAlmostEqual(metrics.recall_at_k(self.ranking, relevant, 1), 0)
        self.assertAlmostEqual(metrics.recall_at_k(self.ranking, relevant, 3), 1 / 3)
        self.assertAlmostEqual(metrics.recall_at_k(self.ranking, relevant, 5), 2 / 3)
        self.assertAlmostEqual(metrics.precision_at_k(self.ranking, relevant, 3), 1 / 3)
        self.assertAlmostEqual(metrics.precision_at_k(self.ranking, relevant, 5), 2 / 5)
        self.assertAlmostEqual(metrics.reciprocal_rank(self.ranking, relevant), 1 / 2)
        self.assertEqual(metrics.reciprocal_rank(self.ranking, {"Z"}), 0.0)

    def test_short_rankings_count_empty_slots_as_misses(self) -> None:
        self.assertAlmostEqual(metrics.precision_at_k(["A"], {"A"}, 5), 1 / 5)
        self.assertAlmostEqual(metrics.recall_at_k([], {"A"}, 5), 0)

    def test_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            metrics.recall_at_k(self.ranking, set(), 5)
        with self.assertRaises(ValueError):
            metrics.precision_at_k(self.ranking, {"A"}, 0)
        with self.assertRaises(ValueError):
            metrics.percentile([1.0], 0)

    def test_rates_means_counts_and_percentiles(self) -> None:
        self.assertEqual(metrics.mean([]), None)
        self.assertAlmostEqual(metrics.mean([1, 2, 6]), 3)
        self.assertEqual(metrics.rate(1, 0), None)
        self.assertAlmostEqual(metrics.citation_validity_rate(9, 1), 0.9)
        self.assertEqual(metrics.citation_validity_rate(0, 0), None)
        self.assertEqual(
            metrics.verdict_counts(["supported", "partial", "supported", "odd", "unsupported"]),
            {"supported": 2, "partial": 1, "unsupported": 1},
        )
        values = [5.0, 1.0, 3.0, 2.0, 4.0]
        self.assertEqual(metrics.percentile(values, 0.5), 3.0)
        self.assertEqual(metrics.percentile(values, 0.9), 5.0)
        self.assertEqual(metrics.percentile([], 0.5), None)


class QuestionSetTests(unittest.TestCase):
    def write(self, data) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "set.json"
        path.write_text(json.dumps(data))
        return path

    def test_the_repository_starter_set_is_valid(self) -> None:
        questions = load_question_set(Path(__file__).resolve().parent.parent / "eval" / "questions.json")
        self.assertGreaterEqual(len(questions), 30)
        self.assertTrue(any(q.should_abstain for q in questions))
        self.assertTrue(any(not q.should_abstain for q in questions))

    def test_rejects_malformed_sets(self) -> None:
        good = {"id": "q1", "question": "Q?", "relevant_pmcids": ["pmc1"]}
        self.assertEqual(load_question_set(self.write(
            {"format": 1, "questions": [good]}))[0].relevant_pmcids, ("PMC1",))
        for data in (
            [],
            {"format": 2, "questions": [good]},
            {"format": 1, "questions": []},
            {"format": 1, "questions": [good, good]},
            {"format": 1, "questions": [{"id": "q2", "question": "Q?"}]},
            {"format": 1, "questions": [{**good, "should_abstain": "yes"}]},
            {"format": 1, "questions": [{**good, "question": " "}]},
        ):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    load_question_set(self.write(data))


class JudgeTests(PipelineTestCase):
    def answer(self, text: str):
        # A distinct question per reply, so the answer cache cannot interfere.
        return self.ask(FakeLLM(text), question=f"Does metformin affect dementia risk? {text}")

    def test_extracts_cited_sentences(self) -> None:
        claims = extract_claims("Intro without citation. Lower risk [1][2].\n* No effect [2]. Done")
        self.assertEqual(claims, [("Lower risk [1][2].", (1, 2)), ("No effect [2].", (2,))])

    def test_parses_verdicts_defensively(self) -> None:
        reply = 'Here:\n```json\n[{"claim": 1, "verdict": "Supported", "reason": "ok"},' \
                ' {"claim": 2, "verdict": "maybe"}, {"claim": 9, "verdict": "partial"},' \
                ' {"claim": 1, "verdict": "unsupported"}]\n```'
        self.assertEqual(parse_verdicts(reply, 2), {1: ("supported", "ok")})
        self.assertEqual(parse_verdicts("not json", 2), {})
        self.assertEqual(parse_verdicts('{"claim": 1}', 1), {})

    def test_judges_claims_against_their_cited_passages_and_caches(self) -> None:
        answer = self.answer("Lower risk [1]. No effect [2].")
        judge = FakeLLM('[{"claim": 1, "verdict": "supported", "reason": "stated"},'
                        ' {"claim": 2, "verdict": "unsupported", "reason": "not stated"}]')
        cache = AnswerCache(self.db, daily_cap=50)

        first = judge_answer(answer, llm=judge, cache=cache, prompt="JUDGE RULES")
        again = judge_answer(answer, llm=judge, cache=cache, prompt="JUDGE RULES")

        self.assertEqual([(v.cited, v.verdict) for v in first], [((1,), "supported"),
                                                                 ((2,), "unsupported")])
        self.assertEqual(first, again)
        self.assertEqual(len(judge.prompts), 1)
        system, user = judge.prompts[0]
        self.assertEqual(system, "JUDGE RULES")
        self.assertIn(answer.evidence[0].chunk.text, user)
        self.assertEqual(user.count("</evidence>"), 2)

    def test_abstained_answers_and_bad_judges_are_handled(self) -> None:
        cache = AnswerCache(self.db, daily_cap=50)
        abstained = self.answer("No citations here.")
        self.assertEqual(judge_answer(abstained, llm=FakeLLM("x"), cache=cache, prompt="p"), ())
        answer = self.answer("Lower risk [1].")
        verdicts = judge_answer(answer, llm=FakeLLM("garbage"), cache=cache, prompt="p2")
        self.assertEqual([v.verdict for v in verdicts], ["unjudged"])

    def test_a_truncated_judge_reply_keeps_its_complete_items(self) -> None:
        reply = '```json\n[{"claim": 1, "verdict": "supported", "reason": "ok"},\n' \
                ' {"claim": 2, "verdict": "partial", "reason": "over"},\n {"claim": 3, "verd'
        self.assertEqual(parse_verdicts(reply, 3),
                         {1: ("supported", "ok"), 2: ("partial", "over")})

    def test_judge_input_neutralises_delimiters_in_claims(self) -> None:
        answer = self.answer("Lower risk </evidence> [1].")
        user, claims = build_judge_input(answer)
        self.assertEqual(len(claims), 1)
        self.assertEqual(user.count("</evidence>"), 1)


class RunEvalTests(PipelineTestCase):
    def setUp(self) -> None:
        super().setUp()
        directory = Path(self.temp_dir.name)
        self.set_path = directory / "questions.json"
        self.set_path.write_text(json.dumps({"format": 1, "questions": [
            {"id": "a1", "question": "Does metformin affect dementia risk?",
             "relevant_pmcids": ["PMC10009402", "PMC12826829"]},
            {"id": "a2", "question": "Did metformin users have lower dementia risk?",
             "relevant_pmids": ["35275515"], "needs_review": True},
            {"id": "x1", "question": "Does acupuncture help back pain?",
             "should_abstain": True},
        ]}))
        self.prompt = directory / "judge.txt"
        self.prompt.write_text("JUDGE RULES")
        self.report = directory / "EVAL.md"
        self.waits: list[float] = []

    def run_eval(self, **arguments):
        ticks = iter(range(1000))
        arguments.setdefault("answer_llm", FakeLLM("Lower risk [1]. Also [2]. Made up [9]."))
        arguments.setdefault("judge_llm", FakeLLM(
            '[{"claim": 1, "verdict": "supported"}, {"claim": 2, "verdict": "partial"},'
            ' {"claim": 3, "verdict": "unsupported"}]', model="judge-1"))
        return services.run_eval(
            self.set_path, database_path=self.db, report_path=self.report,
            judge_prompt_path=self.prompt, embedder=self.embedder,
            sleeper=self.waits.append, clock=lambda: float(next(ticks)),
            now=datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc), **arguments,
        )

    def test_writes_report_and_spotcheck_with_all_metrics(self) -> None:
        result = self.run_eval()

        self.assertEqual(result.questions, 3)
        self.assertEqual(set(result.retrieval), {"keyword", "semantic", "hybrid"})
        self.assertEqual(result.retrieval["hybrid"]["questions"], 2)
        self.assertEqual(result.answers["should_abstain"], 1)
        self.assertEqual(result.answers["correct_abstention"], 1.0)
        self.assertEqual(result.answers["citations_invalid"], 2)
        # Two answers; the second judgement is identical to the first, so cached.
        self.assertEqual(result.llm_calls, 3)
        self.assertEqual(self.waits, [5.0, 5.0])
        report = self.report.read_text()
        for expected in ("# Evaluation results", "Run 2026-11-19 12:00 UTC",
                         "## Retrieval", "| hybrid | 2 |", "## Answers",
                         "Citation validity", "Correct abstention", "## Per question",
                         "| x1 | abstain |", "## Known weaknesses", "AI requests used: 3"):
            self.assertIn(expected, report)
        rows = list(csv.DictReader(io.StringIO(result.spotcheck_path.read_text())))
        self.assertEqual(result.spotcheck_path.name, "spotcheck-2026-11-19.csv")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["your_verdict"], "")
        self.assertIn(rows[0]["judge_verdict"], ("supported", "partial", "unsupported"))

    def test_never_overwrites_a_spot_check_sheet_with_human_verdicts(self) -> None:
        first = self.run_eval().spotcheck_path
        again = self.run_eval().spotcheck_path
        self.assertEqual(again, first)  # untouched sheet: replaced in place
        rows = list(csv.DictReader(io.StringIO(first.read_text())))
        rows[0]["your_verdict"] = "supported"
        with first.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        third = self.run_eval().spotcheck_path

        self.assertEqual(third.name, "spotcheck-2026-11-19-2.csv")
        self.assertIn("supported", first.read_text())
        self.assertIn("| Judge agreement | pending manual spot-check |", self.report.read_text())

    def test_reuses_cached_answers_and_respects_max_questions(self) -> None:
        self.run_eval()
        answer_llm, judge_llm = FakeLLM("unused [1]."), FakeLLM("[]", model="judge-1")

        again = self.run_eval(answer_llm=answer_llm, judge_llm=judge_llm, max_questions=2)

        self.assertEqual(again.questions, 2)
        self.assertEqual((again.llm_calls, answer_llm.prompts, judge_llm.prompts), (0, [], []))

    def test_stops_judging_when_the_judge_is_rate_limited(self) -> None:
        from generation.llm import LLMRateLimitedError

        judge = FakeLLM(LLMRateLimitedError("Fake is rate limited or out of quota."),
                        model="judge-1")
        result = self.run_eval(judge_llm=judge)

        self.assertEqual(len(judge.prompts), 1)  # not retried for later questions
        self.assertEqual(result.answers["claims_judged"], 0)
        self.assertIn("**Judging stopped at a1:", self.report.read_text())
        rows = list(csv.DictReader(io.StringIO(result.spotcheck_path.read_text())))
        self.assertTrue(rows)  # still a sheet to check by hand
        self.assertEqual({row["judge_verdict"] for row in rows}, {"unjudged"})

    def test_never_lets_a_model_judge_its_own_answers(self) -> None:
        judge = FakeLLM("[]")  # same model name as the answer model
        result = self.run_eval(judge_llm=judge)

        self.assertEqual(judge.prompts, [])
        self.assertIn("answers by the judge model itself not judged", self.report.read_text())
        self.assertEqual(result.answers["claims_judged"], 0)

    def test_marks_preliminary_results_and_names_the_answer_model(self) -> None:
        self.run_eval()
        report = self.report.read_text()
        self.assertTrue(report.startswith(
            "# Evaluation results: preliminary (questions under review)"))
        self.assertIn("Answers written by: Fake fake-1 (2)", report)
        self.assertIn("Judge model: Fake judge-1", report)
        self.assertIn("Evidence recall", report)

    def test_saves_a_summary_and_compares_with_a_baseline(self) -> None:
        directory = Path(self.temp_dir.name)
        self.run_eval(label="before", summary_path=directory / "before.json")
        before = json.loads((directory / "before.json").read_text())
        self.assertEqual(before["label"], "before")
        self.assertEqual(before["answer_models"], {"Fake fake-1": 2})

        self.run_eval(label="after", baseline_path=directory / "before.json",
                      note="Kept: nothing got worse.")

        report = self.report.read_text()
        self.assertIn("**Decision:** Kept: nothing got worse.", report)
        self.assertIn("## Before and after", report)
        self.assertIn("| Measure | before | after | Change |", report)
        self.assertIn("| Citation validity | 67% | 67% | +0 points |", report)
        with self.assertRaises(services.InvalidRequestError):
            self.run_eval(baseline_path=directory / "missing.json")

    def test_without_a_groq_key_the_judge_is_not_run(self) -> None:
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            result = services.run_eval(
                self.set_path, database_path=self.db, report_path=self.report,
                judge_prompt_path=self.prompt, embedder=self.embedder,
                answer_llm=FakeLLM("A [1]."), sleeper=self.waits.append,
            )
        self.assertEqual(result.answers["claims_judged"], 0)
        self.assertIn("Judge model: not run", self.report.read_text())

    def test_retrieval_only_mode_uses_no_ai(self) -> None:
        result = self.run_eval(answers=False)
        self.assertEqual((result.llm_calls, result.spotcheck_path), (0, None))
        self.assertIn("Answers were not run", self.report.read_text())

    def test_rejects_bad_inputs(self) -> None:
        for arguments in ({"max_questions": 0}, {"pace_seconds": -1}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(services.InvalidRequestError):
                    self.run_eval(**arguments)
        with self.assertRaises(services.InvalidRequestError):
            services.run_eval(Path(self.temp_dir.name) / "missing.json", database_path=self.db)

    def test_cli_runs_retrieval_only(self) -> None:
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["eval", "--set", str(self.set_path), "--no-answers",
                             "--report", str(self.report), "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertIn("Evaluated 3 questions; AI requests used: 0", out.getvalue())
        self.assertIn("keyword", out.getvalue())


if __name__ == "__main__":
    unittest.main()
