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
class AppPageTests(unittest.TestCase):
    """The four Bento Vitals pages against a small fake bundle; no network, no AI."""

    @classmethod
    def setUpClass(cls) -> None:
        from test_bundle import NOW
        from test_fulltext_services import FakePmcClient
        from test_retrieval import FakeEmbedder
        from test_services import FakePubMedClient, _record

        cls.temp_dir = tempfile.TemporaryDirectory()
        root = Path(cls.temp_dir.name)
        topics = root / "topics.json"
        topics.write_text('{"format": 1, "topics": [{"name": "Attention topics", "query": "adhd", '
                          '"max_citations": 5, "max_fulltext": 2}]}')
        cls.embedder = FakeEmbedder()
        cls.bundle = services.build_bundle(
            topics, output_dir=root / "bundle",
            pubmed_client=FakePubMedClient([_record("35275515")]),
            pmc_client=FakePmcClient(), embedder=cls.embedder, now=lambda: NOW,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {
            "HRI_BUNDLE_DIR": str(Path(self.temp_dir.name) / "bundle"),
            "HRI_BUNDLE_URL": "", "HRI_MODEL_CACHE": str(Path(self.temp_dir.name) / "m"),
            "HRI_DB_PATH": "", "GEMINI_API_KEY": "", "GROQ_API_KEY": "",
        })
        env.start()
        self.addCleanup(env.stop)
        import streamlit as st

        st.cache_resource.clear()  # the cached bundle path is per process
        not_ready = services.EmbedderStatus(False, self.embedder.model_id, "test: no model")
        for patch in (mock.patch("retrieval.embeddings.MODEL_ID", self.embedder.model_id),
                      mock.patch.object(services, "prepare_embedder", return_value=not_ready)):
            patch.start()
            self.addCleanup(patch.stop)

    def app(self) -> AppTest:
        page = AppTest.from_file(APP, default_timeout=30).run()
        self.assertFalse(page.exception, page.exception)
        return page

    def text(self, page: AppTest) -> str:
        return " ".join(e.value for e in (*page.markdown, *page.caption, *page.subheader,
                                           *page.header, *page.title, *page.warning))

    def test_reports_a_missing_bundle_instead_of_crashing(self) -> None:
        with mock.patch.dict(os.environ, {"HRI_BUNDLE_DIR": str(Path(self.temp_dir.name) / "none")}):
            page = AppTest.from_file(APP, default_timeout=30).run()

        self.assertFalse(page.exception)
        self.assertTrue(any("Bundle missing" in e.value for e in page.error))

    def test_search_shows_real_counts_results_and_attribution(self) -> None:
        page = self.app()
        counts = {m.label: m.value for m in page.metric}
        database = str(self.bundle.bundle_path)
        self.assertEqual(counts["Citations"], str(services.get_stats(database_path=database).total_papers))
        self.assertEqual(counts["Open access"],
                         str(services.get_fulltext_stats(database_path=database).documents))

        page.text_input[0].input("Paper")
        page.form_submit_button[0].click().run()

        self.assertIn("1 citation", self.text(page))
        self.assertTrue(any("Paper 35275515" in link.label for link in page.page_link))
        self.assertIn("not affiliated with", self.text(page))  # PubMed attribution
        self.assertIn("not medical advice", self.text(page))

    def test_paper_page_saves_to_a_list_shown_on_the_saved_page(self) -> None:
        page = self.app()
        page.query_params["id"] = "35275515"
        page.switch_page("app_pages/paper.py").run()
        self.assertFalse(page.exception, page.exception)
        self.assertIn("Paper 35275515", self.text(page))
        self.assertIn("Don’t enter personal or patient information", self.text(page))

        save = next(b for b in page.form_submit_button if b.label == "Save")
        save.click().run()
        self.assertTrue(any("Reading list" in s.value for s in page.success))

        page.switch_page("app_pages/saved.py").run()
        self.assertIn("Reading list", self.text(page))
        self.assertTrue(any("Paper 35275515" in link.label for link in page.page_link))
        self.assertTrue(page.download_button)  # CSV export offered

    def test_ask_says_not_enough_evidence_and_counts_the_question(self) -> None:
        page = self.app()
        page.switch_page("app_pages/ask.py").run()
        self.assertIn("Don’t enter personal or patient information", self.text(page))

        page.text_area[0].input("Zyxqv blorft?")  # matches no stored passage
        page.form_submit_button[0].click().run()

        self.assertFalse(page.exception, page.exception)
        self.assertIn("Not enough evidence", self.text(page))
        self.assertIn("4 of 5 questions left", self.text(page))
        self.assertIn("not medical advice", self.text(page))


if __name__ == "__main__":
    unittest.main()
