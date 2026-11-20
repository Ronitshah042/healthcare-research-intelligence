"""Tests for building, fetching, and checking the deployment data bundle (fakes only)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from test_fulltext_services import FakePmcClient
from test_retrieval import FakeEmbedder
from test_services import FakePubMedClient, _record

from hri import services
from ingestion.storage import PaperStore

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


class BundleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.folder = Path(self.temp_dir.name)
        self.topics = self.folder / "topics.json"
        self.topics.write_text(json.dumps({"format": 1, "topics": [
            {"name": "ADHD", "query": "adhd employment", "max_citations": 5, "max_fulltext": 2},
            {"name": "Veterans", "query": "veterans heart", "max_citations": 5, "max_fulltext": 0},
        ]}))
        self.embedder = FakeEmbedder()

    def build(self, **arguments):
        return services.build_bundle(
            self.topics, output_dir=self.folder / "dist",
            pubmed_client=FakePubMedClient([_record("35275515"), _record("222")]),
            pmc_client=FakePmcClient(), embedder=self.embedder, now=lambda: NOW, **arguments,
        )


class BuildBundleTests(BundleTestCase):
    def test_builds_a_compact_read_only_bundle_with_a_manifest(self) -> None:
        result = self.build()

        manifest = json.loads(result.manifest_path.read_text())
        self.assertEqual(manifest, result.manifest)
        self.assertEqual(manifest["counts"]["papers"], 2)
        self.assertEqual(manifest["counts"]["articles"], 2)
        self.assertEqual(manifest["counts"]["passages"], manifest["counts"]["vectors"])
        self.assertEqual(manifest["licences"], {"CC BY": 1, "CC0": 1})
        self.assertEqual(manifest["embedding_model"], self.embedder.model_id)
        self.assertEqual(manifest["built_at"], "2026-11-20T09:00:00Z")
        self.assertEqual(manifest["sha256"],
                         hashlib.sha256(result.bundle_path.read_bytes()).hexdigest())
        self.assertEqual(manifest["size_bytes"], result.size_bytes)
        self.assertEqual([t["topic"] for t in manifest["topics"]], ["ADHD", "Veterans"])
        with sqlite3.connect(result.bundle_path) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            for table in ("answer_cache", "saved_lists", "fulltext_rejections",
                          "document_sections"):
                self.assertEqual(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        store = PaperStore(result.bundle_path, read_only=True)
        self.assertEqual(store.stats().total_papers, 2)

    def test_the_bundle_is_searchable_read_only_and_unchanged(self) -> None:
        from ingestion.fulltext_store import FullTextStore
        from retrieval.hybrid import hybrid_search

        result = self.build()
        before = result.bundle_path.read_bytes()

        store = FullTextStore(result.bundle_path, read_only=True)

        self.assertTrue(hybrid_search(store, self.embedder, "veterans", limit=3))
        self.assertEqual(result.bundle_path.read_bytes(), before)

    def test_public_bundles_never_take_non_commercial_text(self) -> None:
        client = FakePmcClient()
        from test_fulltext_services import CC0, metadata
        client.metadata[f"{CC0}.1"] = metadata(f"{CC0}.1", licence="CC BY-NC")
        result = services.build_bundle(
            self.topics, output_dir=self.folder / "dist",
            pubmed_client=FakePubMedClient([_record("1")]), pmc_client=client,
            embedder=self.embedder, now=lambda: NOW,
        )
        self.assertEqual(result.manifest["licences"], {"CC BY": 1})
        self.assertEqual(result.manifest["rejections"], {"licence": 1})

    def test_rejects_bad_topics_files(self) -> None:
        for data in ({"format": 1, "topics": []}, {"topics": [{"name": "x", "query": "y"}]},
                     {"format": 1, "topics": [{"name": "x"}]},
                     {"format": 1, "topics": [{"name": "x", "query": "y", "max_fulltext": 99}]}):
            with self.subTest(data=data):
                self.topics.write_text(json.dumps(data))
                with self.assertRaises(services.InvalidRequestError):
                    services.load_topics(self.topics)

    def test_the_repository_topics_file_is_valid(self) -> None:
        topics = services.load_topics(Path(__file__).resolve().parent.parent / "topics.json")
        # About 60% of candidates pass CC0/CC BY, so 175 candidates give ~100 articles.
        self.assertEqual(sum(t["max_fulltext"] for t in topics), 175)


class FetchAndCheckTests(BundleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.built = self.build()
        self.files = {
            services.MANIFEST_NAME: self.built.manifest_path.read_bytes(),
            services.BUNDLE_NAME: self.built.bundle_path.read_bytes(),
        }
        self.requests: list[str] = []
        self.cache = self.folder / "cache"

    def opener(self, url: str) -> bytes:
        self.requests.append(url)
        return self.files[url.rsplit("/", 1)[1]]

    def fetch(self, url="https://github.com/o/r/releases/latest/download"):
        return services.fetch_bundle(url, directory=self.cache, opener=self.opener)

    def test_downloads_once_verifies_and_reuses(self) -> None:
        path = self.fetch()
        self.assertEqual(path.read_bytes(), self.files[services.BUNDLE_NAME])
        self.assertEqual(len(self.requests), 2)
        self.fetch()
        self.assertEqual(len(self.requests), 3)  # manifest only; cached bundle reused

    def test_refuses_corrupt_downloads_and_other_hosts(self) -> None:
        self.files[services.BUNDLE_NAME] = b"corrupted"
        with self.assertRaises(services.InvalidRequestError):
            self.fetch()
        self.assertFalse((self.cache / services.BUNDLE_NAME).exists())
        for url in ("http://github.com/o/r", "https://evil.example/o/r",
                    "https://github.com.evil.example/x"):
            with self.subTest(url=url):
                with self.assertRaises(services.InvalidRequestError):
                    self.fetch(url)

    def test_self_check_passes_and_reports_problems(self) -> None:
        bundle = self.fetch()
        good = services.check_deployment(bundle, embedder_id=self.embedder.model_id)
        self.assertTrue(good.ok, good.problems)

        wrong_model = services.check_deployment(bundle, embedder_id="other-model@1")
        self.assertFalse(wrong_model.ok)
        self.assertIn("rebuild the bundle", wrong_model.problems[0])
        missing = services.check_deployment(self.folder / "nowhere" / services.BUNDLE_NAME)
        self.assertEqual(len(missing.problems), 2)


if __name__ == "__main__":
    unittest.main()
