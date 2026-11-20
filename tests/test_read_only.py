"""Tests for read-only databases and the in-memory helpers of a deployed app."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_answer import FakeLLM, PipelineTestCase

from generation.cache import AnswerCache, DailyCapReachedError
from generation.llm import LLMReply
from hri import services
from ingestion.pipeline import StandardisedPaper
from ingestion.storage import PaperStorageError, PaperStore


def _paper(pmid: str) -> StandardisedPaper:
    return StandardisedPaper(
        pmid=pmid, doi=None, title=f"Paper {pmid}", authors=("Ada Lovelace",),
        journal="Example Journal", publication_year=2024, abstract_available=False,
        source_identifier=f"PMID:{pmid}", source_url=None, source_name="PubMed",
    )


class ReadOnlyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "bundle.sqlite3"
        PaperStore(self.path)  # build the current layout

    def test_reads_without_writing_or_creating_side_files(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
        before = sorted(p.name for p in self.path.parent.iterdir())

        store = PaperStore(self.path, read_only=True)

        self.assertEqual(store.list_papers(limit=5, offset=0), ())
        self.assertEqual(store.stats().total_papers, 0)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), before)

    def test_refuses_writes_missing_files_and_other_versions(self) -> None:
        store = PaperStore(self.path, read_only=True)
        with self.assertRaises(PaperStorageError):
            store.save(_paper("123"))
        with self.assertRaises(PaperStorageError):
            PaperStore(self.path.with_name("missing.sqlite3"), read_only=True)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA user_version = 1")  # stays in the -wal file
        with self.assertRaises(PaperStorageError) as raised:
            PaperStore(self.path, read_only=True)
        self.assertIn("unmerged changes", str(raised.exception))
        connection.execute("PRAGMA journal_mode = DELETE")  # merges and removes -wal
        connection.close()
        with self.assertRaises(PaperStorageError) as raised:
            PaperStore(self.path, read_only=True)
        self.assertIn("Rebuild the bundle", str(raised.exception))

    def test_the_environment_variable_turns_read_only_on(self) -> None:
        with mock.patch.dict(os.environ, {"HRI_DB_READ_ONLY": "1"}):
            self.assertTrue(PaperStore(self.path).read_only)
        with mock.patch.dict(os.environ, {"HRI_DB_READ_ONLY": ""}):
            self.assertFalse(PaperStore(self.path).read_only)


class SharedMemoryCacheTests(PipelineTestCase):
    def test_memory_cache_and_cap_hold_across_instances(self) -> None:
        first = AnswerCache(self.db, daily_cap=1, read_only=True)
        first.put("k", LLMReply("text", "Fake", "fake-1"))
        first.count_request()

        second = AnswerCache(self.db, daily_cap=1, read_only=True)

        self.assertEqual(second.get("k"), LLMReply("text", "Fake", "fake-1"))
        self.assertEqual(second.requests_today(), 1)
        with self.assertRaises(DailyCapReachedError):
            second.count_request()

    def test_ask_question_on_a_read_only_database_caches_and_caps(self) -> None:
        llm = FakeLLM("Lower risk [1].")
        with mock.patch.dict(os.environ, {"HRI_DB_READ_ONLY": "1",
                                          "HRI_LLM_DAILY_REQUEST_CAP": "1"}):
            first = self.ask(llm, question="metformin dementia read only")
            again = self.ask(llm, question="metformin dementia read only")
            other = self.ask(llm, question="metformin dementia another question")

        self.assertEqual((first.cached, again.cached), (False, True))
        self.assertEqual(len(llm.prompts), 1)
        self.assertEqual(other.abstain_reason, "busy")


class SessionHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = Path(self.temp_dir.name) / "hri.sqlite3"
        store = PaperStore(self.db)
        for pmid in ("111", "222"):
            store.save(_paper(pmid))

    def test_session_lists_work_without_writing_the_database(self) -> None:
        lists = services.SessionSavedLists(database_path=self.db)
        lists.create("To read")
        lists.add("to read", "111")
        lists.add("To read", "111")  # no duplicate
        lists.add("To read", "222")
        lists.rename("To read", "Reading")

        self.assertEqual(lists.names(), ("Reading",))
        self.assertEqual([p.pmid for p in lists.papers("reading")], ["111", "222"])
        self.assertTrue(lists.remove("Reading", "111"))
        self.assertIn("222", lists.export("Reading", "csv"))
        self.assertTrue(lists.export("Reading", "citations").strip())
        for bad in (lambda: lists.create("reading"), lambda: lists.papers("Missing"),
                    lambda: lists.export("Reading", "pdf"), lambda: lists.create(" ")):
            with self.assertRaises(services.InvalidRequestError):
                bad()
        with self.assertRaises(services.PaperNotFoundError):
            lists.add("Reading", "999")
        lists.delete("Reading")
        self.assertEqual(lists.names(), ())
        self.assertEqual(services.get_lists(database_path=self.db), ())  # nothing written

    def test_visitor_quota(self) -> None:
        quota = services.VisitorQuota(limit=2)
        self.assertEqual([quota.try_use() for _ in range(3)], [True, True, False])
        self.assertEqual(quota.remaining, 0)
        self.assertEqual(services.VisitorQuota().limit, services.DEFAULT_VISITOR_QUESTIONS)


if __name__ == "__main__":
    unittest.main()
