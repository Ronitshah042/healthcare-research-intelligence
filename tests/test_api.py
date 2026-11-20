"""Tests for the FastAPI interface, with TestClient and fakes only.

Skipped when FastAPI is not installed (it needs Python 3.11+ and the
optional ``.[api]`` install).
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

try:
    from fastapi.testclient import TestClient
except ImportError:  # optional dependency
    TestClient = None

from test_answer import FakeLLM, PipelineTestCase
from test_services import FakePubMedClient, _record

from generation.llm import FallbackLLM
from hri import services


@unittest.skipIf(TestClient is None, "FastAPI is not installed (pip install -e '.[api]')")
class ApiTests(PipelineTestCase):
    def setUp(self) -> None:
        super().setUp()
        from api.app import create_app

        self.client = TestClient(create_app(database_path=self.db))
        self.pubmed = FakePubMedClient([_record("111"), _record("222")])
        patch = mock.patch.object(services, "build_pubmed_client", return_value=self.pubmed)
        patch.start()
        self.addCleanup(patch.stop)
        token = mock.patch.dict(os.environ, {"HRI_ADMIN_TOKEN": "s3cret"})
        token.start()
        self.addCleanup(token.stop)
        self.admin = {"Authorization": "Bearer s3cret"}

    def ingest(self) -> None:
        response = self.client.post("/ingest", json={"query": "sleep"}, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)

    def test_health_and_stats(self) -> None:
        self.ingest()
        health = self.client.get("/health").json()
        self.assertEqual((health["status"], health["papers"], health["fulltext_articles"]),
                         ("ok", 2, 2))
        stats = self.client.get("/stats").json()
        self.assertEqual((stats["total_papers"], stats["fulltext_passages"]), (2, 4))

    def test_search_and_paper_carry_attribution(self) -> None:
        self.ingest()
        found = self.client.get("/search", params={"q": "paper", "page_size": 1}).json()
        self.assertEqual((found["total_count"], found["total_pages"]), (2, 2))
        self.assertEqual(found["attribution"]["source_name"],
                         services.PUBMED_ATTRIBUTION.source_name)
        paper = self.client.get("/papers/111").json()
        self.assertEqual(paper["paper"]["pmid"], "111")
        self.assertIsNotNone(paper["attribution"])

    def test_errors_share_one_format(self) -> None:
        cases = (
            (self.client.get("/papers/999"), 404, "not_found"),
            (self.client.get("/papers/not-an-id"), 400, "invalid_request"),
            (self.client.get("/search", params={"q": "x", "page": 0}), 422, "invalid_request"),
            (self.client.get("/search", params={"q": "(", "strategy": "boolean"}), 400,
             "invalid_request"),
            (self.client.post("/ask", json={"question": ""}), 422, "invalid_request"),
        )
        for response, status, code in cases:
            with self.subTest(status=status, code=code):
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(set(response.json()), {"error"})
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertTrue(response.json()["error"]["message"])

    def test_ask_returns_answer_citations_and_disclaimer(self) -> None:
        fake = FakeLLM("Lower risk [1].")
        with mock.patch("hri.answer_services.build_default_llm",
                        side_effect=lambda before_request: FallbackLLM(
                            [fake], before_request=before_request)), \
             mock.patch("hri.answer_services.loaded_embedder",
                        return_value=(self.embedder, None)):
            response = self.client.post(
                "/ask", json={"question": "Does metformin affect dementia risk?"})

        body = response.json()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(body["abstained"])
        self.assertEqual(body["citations"][0]["number"], 1)
        self.assertIn("not medical advice", body["disclaimer"])

    def test_admin_endpoints_need_the_token(self) -> None:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = self.client.post("/ingest", json={"query": "x"}, headers=headers)
            self.assertEqual((response.status_code, response.json()["error"]["code"]),
                             (401, "unauthorized"))
        with mock.patch.dict(os.environ, {"HRI_ADMIN_TOKEN": ""}):
            response = self.client.post("/index", headers=self.admin)
        self.assertEqual((response.status_code, response.json()["error"]["code"]),
                         (503, "admin_disabled"))
        self.assertEqual(self.pubmed.calls, [])

    def test_index_embeds_passages_through_the_service(self) -> None:
        real = services.embed_passages

        def embed(*, database_path=None):
            return real(database_path=database_path, embedder=self.embedder)

        with mock.patch.object(services, "embed_passages", side_effect=embed):
            response = self.client.post("/index", headers=self.admin)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["model_id"], self.embedder.model_id)


if __name__ == "__main__":
    unittest.main()
