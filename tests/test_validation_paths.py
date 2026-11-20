"""Pin the input checks listed as untested in docs/AUDIT.md (proposal P6)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ingestion.pubmed import PubMedClient, PubMedRequestValidationError
from ingestion.pubmed_config import PubMedClientConfig, PubMedConfigurationError
from ingestion.storage import PaperStore


def _no_request(url, timeout):
    raise AssertionError("no request expected")


class PubMedClientValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = PubMedClient(
            PubMedClientConfig(tool="hri", email="dev@example.org"),
            transport=_no_request, clock=lambda: 0.0, sleeper=lambda seconds: None,
        )

    def test_year_filters_reject_bad_years_before_any_request(self) -> None:
        for arguments in ({"publication_year": "2020"}, {"publication_year": True},
                          {"publication_year": 0},
                          {"publication_year": 2020, "publication_year_end": 2019}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(PubMedRequestValidationError):
                    self.client.search_title("heart", **arguments)

    def test_blank_identifiers_return_an_empty_page_without_a_request(self) -> None:
        for lookup in (self.client.lookup_doi, self.client.lookup_pmid):
            with self.subTest(lookup=lookup.__name__):
                page = lookup("   ")
                self.assertEqual((page.total_count, page.records), (0, ()))


class ConfigValidationTests(unittest.TestCase):
    def test_rejects_wrong_types_passed_directly(self) -> None:
        base = {"tool": "hri", "email": "dev@example.org"}
        for overrides in ({"tool": 5}, {"timeout_seconds": "ten"},
                          {"requests_per_second": float("nan")}, {"max_retries": -1},
                          {"max_retries": True}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(PubMedConfigurationError):
                    PubMedClientConfig(**{**base, **overrides})


class PaperStoreValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = PaperStore(Path(self.temp_dir.name) / "papers.sqlite3")

    def test_rejects_bad_paging_years_and_identifiers(self) -> None:
        cases = (
            lambda: self.store.list_papers(limit=0, offset=0),
            lambda: self.store.list_papers(limit=5, offset=-1),
            lambda: self.store.list_papers(limit=True, offset=0),
            lambda: self.store.search("x", year_from="2020"),
            lambda: self.store.get(pmid="12a"),
            lambda: self.store.get(pmid="1", doi="10.1234/x"),
            lambda: self.store.get(),
        )
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(ValueError):
                    case()


if __name__ == "__main__":
    unittest.main()
