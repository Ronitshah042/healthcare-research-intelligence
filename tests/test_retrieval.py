"""Tests for embeddings, hybrid search, and the retrieval services.

Only ``FakeEmbedder`` is used: no network access and no model download.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest import mock

from test_fulltext_store import CVD_DOCUMENT, DOCUMENT

from hri import retrieval_services, services
from ingestion.chunking import chunk_sections
from ingestion.fulltext import ArticleSection
from ingestion.fulltext_store import FullTextStore
from retrieval import embeddings
from retrieval.embeddings import EmbedderUnavailableError, from_blob, to_blob, unit
from retrieval.hybrid import hybrid_search, vector_search

# Words the fake model treats as meaning the same thing.
SYNONYMS = {"heart": "cardiac", "myocardial": "cardiac", "infarction": "attack"}


class FakeEmbedder:
    """Deterministic bag-of-words vectors, with a few synonyms merged."""

    model_id = "fake-model@1"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return self._vector(text)

    def count_tokens(self, text: str) -> int:
        # Real tokenizers produce more tokens than words; characters exaggerate that.
        return len(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * 64
        for word in text.casefold().split():
            word = SYNONYMS.get(word.strip(".,"), word.strip(".,"))
            vector[zlib.crc32(word.encode()) % 64] += 1.0
        return unit(vector)


def sections(*texts: str, name: str = "results") -> tuple[ArticleSection, ...]:
    return tuple(ArticleSection(name, None, (text,)) for text in texts)


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "hri.sqlite3"
        self.store = FullTextStore(self.db)
        self.embedder = FakeEmbedder()
        cache = mock.patch.dict(
            "os.environ", {"HRI_MODEL_CACHE": str(Path(self.temp_dir.name) / "models")}
        )
        cache.start()
        self.addCleanup(cache.stop)
        self.addCleanup(retrieval_services._LOADED.clear)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def save(self, document, *texts: str, name: str = "results") -> None:
        parts = sections(*texts, name=name)
        self.store.save(document, parts, chunk_sections(parts))

    def embed(self) -> services.EmbedPassagesResult:
        return services.embed_passages(database_path=self.db, embedder=self.embedder)


class VectorTests(unittest.TestCase):
    def test_vectors_round_trip_as_float32_and_are_unit_length(self) -> None:
        vector = unit([3.0, 4.0])
        self.assertEqual(vector, [0.6, 0.8])
        self.assertEqual(len(to_blob(vector)), 8)
        self.assertEqual(
            [round(value, 6) for value in from_blob(to_blob(vector))], [0.6, 0.8]
        )
        self.assertEqual(unit([0.0, 0.0]), [0.0, 0.0])


class HybridSearchTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.save(DOCUMENT, "Heart attack risk rose in veterans.",
                  "Sleep quality improved in students.")
        self.save(CVD_DOCUMENT, "Myocardial infarction was rare.",
                  "Veterans reported sleep problems.", name="methods")
        self.embed()

    def test_vector_search_finds_meaning_that_keywords_miss(self) -> None:
        self.assertEqual(self.store.search("cardiac", limit=5), ())

        matches = vector_search(self.store, self.embedder, "cardiac", limit=2)

        self.assertEqual(
            {match.chunk.text for match in matches},
            {"Heart attack risk rose in veterans.", "Myocardial infarction was rare."},
        )
        self.assertGreater(matches[0].score, 0)
        self.assertEqual(matches[0].document.attribution[-1], ".")

    def test_hybrid_ranks_passages_found_both_ways_first(self) -> None:
        matches = hybrid_search(self.store, self.embedder, "heart attack", limit=3)

        self.assertEqual(matches[0].chunk.text, "Heart attack risk rose in veterans.")
        self.assertIn("Myocardial infarction was rare.",
                      [match.chunk.text for match in matches])
        scores = [match.score for match in matches]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_hybrid_respects_sections_and_rejects_empty_queries(self) -> None:
        matches = hybrid_search(
            self.store, self.embedder, "sleep", limit=5, sections=("methods",)
        )
        self.assertEqual({match.chunk.section_name for match in matches}, {"methods"})
        with self.assertRaises(ValueError):
            hybrid_search(self.store, self.embedder, "***", limit=5)

    def test_vectors_leave_with_their_passages(self) -> None:
        self.store.reject("PMC10009402", "retracted", recorded_at="2026-11-18T00:00:00Z")

        texts = {m.chunk.text for m in vector_search(
            self.store, self.embedder, "heart veterans sleep", limit=10)}

        self.assertEqual(texts, {"Myocardial infarction was rare.",
                                 "Veterans reported sleep problems."})
        self.assertEqual(len(self.store.embeddings(self.embedder.model_id)), 2)


class EmbedPassagesTests(StoreTestCase):
    def test_embeds_only_new_passages_and_counts_long_ones(self) -> None:
        long_text = " ".join(["word"] * 120)  # 599 characters, one passage
        self.save(DOCUMENT, "Short passage.", long_text)

        with self.assertLogs("hri.retrieval_services", "INFO") as logs:
            first = self.embed()
        second = self.embed()

        self.assertEqual((first.embedded, first.over_token_limit), (2, 1))
        self.assertEqual(first.longest_tokens, 599)
        self.assertIn("1 exceeded 512 tokens", logs.output[0])
        self.assertEqual(second.embedded, 0)
        self.assertEqual(self.store.count_unembedded(self.embedder.model_id), 0)

    def test_replacing_an_article_needs_new_embeddings(self) -> None:
        self.save(DOCUMENT, "Old text.")
        self.embed()
        self.save(DOCUMENT, "New text.")

        self.assertEqual(self.store.count_unembedded(self.embedder.model_id), 1)
        self.assertEqual(self.embed().embedded, 1)


class SearchServiceTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.save(DOCUMENT, "Heart attack risk rose in veterans.")
        self.save(CVD_DOCUMENT, "Myocardial infarction was rare.")

    def test_hybrid_search_with_a_ready_model(self) -> None:
        before = services.search_passages(
            "heart", database_path=self.db, embedder=self.embedder
        )
        self.assertEqual(before.method, "hybrid")
        self.assertIn("2 passages have no embedding yet", before.notice)

        self.embed()
        result = services.search_passages(
            "heart", database_path=self.db, embedder=self.embedder
        )

        self.assertEqual((result.method, result.notice), ("hybrid", None))
        self.assertEqual(len(result.matches), 2)
        self.assertEqual(self.embedder.queries, ["heart", "heart"])

    def test_falls_back_to_keyword_search_and_says_why(self) -> None:
        with mock.patch.object(embeddings, "prepare") as download:
            result = services.search_passages("heart", database_path=self.db)

        download.assert_not_called()  # a search never downloads
        self.assertEqual(result.method, "keyword")
        self.assertTrue(result.notice.startswith("Keyword-only search: "))
        self.assertEqual(len(result.matches), 1)

    def test_prepare_embedder_reports_problems_instead_of_raising(self) -> None:
        with mock.patch.dict(sys.modules, {"fastembed": None}):
            status = services.prepare_embedder()

        self.assertFalse(status.available)
        self.assertEqual(status.model_id, embeddings.MODEL_ID)
        self.assertIn("pip install -e '.[embeddings]'", status.message)

    def test_embed_passages_raises_when_the_model_is_unavailable(self) -> None:
        with mock.patch.dict(sys.modules, {"fastembed": None}):
            with self.assertRaises(services.EmbedderUnavailableError):
                services.embed_passages(database_path=self.db)


class PrepareModelTests(unittest.TestCase):
    """The download path, with fake ``fastembed`` and ``huggingface_hub`` modules."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp_dir.name)
        self.calls: list[dict] = []
        self.content = b"model bytes"

        def snapshot_download(**arguments):
            self.calls.append(arguments)
            target = Path(arguments["local_dir"])
            target.mkdir(parents=True, exist_ok=True)
            for name in embeddings.MODEL_FILES:
                (target / name).write_bytes(
                    self.content if name == embeddings.MODEL_FILE else b"{}"
                )
            return str(target)

        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = snapshot_download  # type: ignore[attr-defined]
        patches = (
            mock.patch.dict(sys.modules, {
                "fastembed": types.ModuleType("fastembed"), "huggingface_hub": hub,
            }),
            mock.patch.object(embeddings, "MODEL_FILE_SIZE", len(self.content)),
            mock.patch.object(
                embeddings, "MODEL_FILE_SHA256",
                hashlib.sha256(self.content).hexdigest(),
            ),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_downloads_the_pinned_revision_once_and_verifies_it(self) -> None:
        target = embeddings.prepare(self.cache)
        again = embeddings.prepare(self.cache)

        self.assertEqual(target, again)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["repo_id"], embeddings.MODEL_REPOSITORY)
        self.assertEqual(self.calls[0]["revision"], embeddings.MODEL_REVISION)
        self.assertEqual(sorted(self.calls[0]["allow_patterns"]),
                         sorted(embeddings.MODEL_FILES))
        self.assertTrue((target / ".verified").exists())

    def test_deletes_a_model_that_fails_its_hash_check(self) -> None:
        self.content = b"tampered!!!"  # same length, different hash

        with self.assertRaises(EmbedderUnavailableError) as raised:
            embeddings.prepare(self.cache)

        self.assertIn("SHA-256", str(raised.exception))
        self.assertFalse(embeddings.model_dir(self.cache).exists())

    def test_a_failed_download_is_reported_without_details(self) -> None:
        def fail(**arguments):
            raise OSError("connection refused to secret-host")

        sys.modules["huggingface_hub"].snapshot_download = fail  # type: ignore[attr-defined]

        with self.assertRaises(EmbedderUnavailableError) as raised:
            embeddings.prepare(self.cache)
        self.assertNotIn("secret-host", str(raised.exception))
        self.assertIn("OSError", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
