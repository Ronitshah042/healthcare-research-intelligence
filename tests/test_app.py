"""Tests for the deployment settings helper and the placeholder Streamlit page."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hri import app_config, services

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # optional dependency
    AppTest = None

APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")


class AppConfigTests(unittest.TestCase):
    def test_copies_known_secrets_without_overriding_the_environment(self) -> None:
        env = {"GROQ_API_KEY": "from-env"}
        applied = app_config.apply_settings(
            {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "from-secrets", "UNKNOWN": "x",
             "HRI_BUNDLE_URL": " "}, env)

        self.assertEqual(env["GEMINI_API_KEY"], "g")
        self.assertEqual(env["GROQ_API_KEY"], "from-env")
        self.assertNotIn("UNKNOWN", env)
        self.assertNotIn("HRI_BUNDLE_URL", env)
        self.assertEqual((env["HRI_DB_READ_ONLY"], env["HRI_LLM_DAILY_REQUEST_CAP"]), ("1", "50"))
        self.assertIn("GEMINI_API_KEY", applied)
        self.assertNotIn("g", applied)  # names only, never values

    def test_explicit_settings_win_over_app_defaults(self) -> None:
        env = {"HRI_LLM_DAILY_REQUEST_CAP": "10"}
        app_config.apply_settings({"HRI_LLM_DAILY_REQUEST_CAP": "99"}, env)
        self.assertEqual(env["HRI_LLM_DAILY_REQUEST_CAP"], "10")
        app_config.apply_settings(None, {})

    def test_get_setting_prefers_environment_then_secrets(self) -> None:
        self.assertEqual(app_config.get_setting("X", {"X": "s"}, environment={"X": "e"}), "e")
        self.assertEqual(app_config.get_setting("X", {"X": "s"}, environment={}), "s")
        self.assertEqual(app_config.get_setting("X", {}, "d", environment={}), "d")


@unittest.skipIf(AppTest is None, "Streamlit is not installed (pip install -e '.[app]')")
class PlaceholderPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        env = mock.patch.dict(os.environ, {
            "HRI_BUNDLE_DIR": str(Path(self.temp_dir.name) / "bundle"),
            "HRI_BUNDLE_URL": "", "HRI_MODEL_CACHE": str(Path(self.temp_dir.name) / "m"),
            "HRI_DB_PATH": "", "GEMINI_API_KEY": "", "GROQ_API_KEY": "",
        })
        env.start()
        self.addCleanup(env.stop)
        import streamlit as st

        st.cache_resource.clear()  # the cached bundle path is per process

    def test_reports_a_missing_bundle_instead_of_crashing(self) -> None:
        page = AppTest.from_file(APP, default_timeout=30).run()

        self.assertFalse(page.exception)
        self.assertIn("Placeholder page, real UI coming", page.warning[0].value)
        self.assertTrue(any("Bundle missing" in e.value for e in page.error))

    def test_shows_stats_and_search_with_a_good_bundle(self) -> None:
        from test_bundle import NOW
        from test_fulltext_services import FakePmcClient
        from test_retrieval import FakeEmbedder
        from test_services import FakePubMedClient, _record

        topics = Path(self.temp_dir.name) / "topics.json"
        topics.write_text('{"format": 1, "topics": [{"name": "t", "query": "adhd", '
                          '"max_citations": 5, "max_fulltext": 2}]}')
        embedder = FakeEmbedder()
        services.build_bundle(
            topics, output_dir=Path(self.temp_dir.name) / "bundle",
            pubmed_client=FakePubMedClient([_record("35275515")]),
            pmc_client=FakePmcClient(), embedder=embedder, now=lambda: NOW,
        )
        not_ready = services.EmbedderStatus(False, embedder.model_id, "test: no model")
        with mock.patch("retrieval.embeddings.MODEL_ID", embedder.model_id), \
             mock.patch.object(services, "prepare_embedder", return_value=not_ready):
            page = AppTest.from_file(APP, default_timeout=30).run()
            self.assertFalse(page.exception, page.exception)
            self.assertFalse(page.error, [e.value for e in page.error])
            page.text_input[0].input("Paper").run()

        text = " ".join(m.value for m in page.markdown)
        self.assertIn("1 citations", text)
        self.assertIn("Paper 35275515", text)
        self.assertTrue(page.caption)  # PubMed attribution shown


if __name__ == "__main__":
    unittest.main()
