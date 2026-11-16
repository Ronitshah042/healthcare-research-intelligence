"""Tests for the thin PubMed search pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import unittest

from ingestion.metadata import PaperMetadata
from ingestion.pipeline import (
    SearchPipeline,
    SearchRequest,
    SearchRequestValidationError,
)
from ingestion.pubmed import (
    PUBMED_ATTRIBUTION,
    PubMedSearchPage,
    PubMedSearchRecord,
    SearchMode,
)


@dataclass
class FakePubMedClient:
    """Record routed calls and return a prepared search page without HTTP."""

    page: PubMedSearchPage
    calls: list[tuple[str, str, dict[str, int | None]]] = field(default_factory=list)

    def search_title(self, query: str, **arguments: int | None) -> PubMedSearchPage:
        return self._record("search_title", query, arguments)

    def search_title_abstract(
        self, query: str, **arguments: int | None
    ) -> PubMedSearchPage:
        return self._record("search_title_abstract", query, arguments)

    def search_author(self, query: str, **arguments: int | None) -> PubMedSearchPage:
        return self._record("search_author", query, arguments)

    def lookup_doi(self, query: str, **arguments: int | None) -> PubMedSearchPage:
        return self._record("lookup_doi", query, arguments)

    def lookup_pmid(self, query: str, **arguments: int | None) -> PubMedSearchPage:
        return self._record("lookup_pmid", query, arguments)

    def _record(
        self, method_name: str, query: str, arguments: dict[str, int | None]
    ) -> PubMedSearchPage:
        self.calls.append((method_name, query, arguments))
        return self.page


class SearchPipelineTests(unittest.TestCase):
    """Verify deterministic routing and result normalisation."""

    @staticmethod
    def _page(
        *,
        search_mode: SearchMode = SearchMode.TITLE,
        fallback_used: bool = False,
        invalid_record_count: int = 0,
        records: tuple[object, ...] | None = None,
    ) -> PubMedSearchPage:
        page_records = records if records is not None else (SearchPipelineTests._record(),)
        return PubMedSearchPage(
            records=page_records,  # type: ignore[arg-type]
            total_count=4,
            page=2,
            page_size=10,
            search_mode=search_mode,
            fallback_used=fallback_used,
            invalid_record_count=invalid_record_count,
        )

    @staticmethod
    def _record(
        *,
        pmid: str = "12345",
        doi: str | None = "10.1234/example",
        title: str = "Example paper",
        authors: tuple[str, ...] = ("Ada Lovelace",),
        journal_title: str | None = "Example Journal",
        publication_year: int = 2024,
        abstract: str | None = None,
        abstract_available: bool = False,
    ) -> PubMedSearchRecord:
        source_identifier = f"PMID:{pmid}" if pmid else f"DOI:{doi or 'unknown'}"
        metadata = PaperMetadata(
            title=title,
            authors=authors,
            publication_year=publication_year,
            source_identifier=source_identifier,
            abstract=abstract,
            source_url=(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None),
        )
        return PubMedSearchRecord(
            metadata=metadata,
            pmid=pmid,
            doi=doi,
            journal_title=journal_title,
            citation_date=str(publication_year),
            abstract_available=abstract_available,
        )

    def test_routes_each_search_mode_with_shared_request_options(self) -> None:
        routes = (
            (SearchMode.TITLE, "search_title"),
            (SearchMode.TITLE_ABSTRACT, "search_title_abstract"),
            (SearchMode.AUTHOR, "search_author"),
            (SearchMode.DOI, "lookup_doi"),
            (SearchMode.PMID, "lookup_pmid"),
        )

        for search_mode, expected_method in routes:
            with self.subTest(search_mode=search_mode):
                client = FakePubMedClient(self._page(search_mode=search_mode))
                pipeline = SearchPipeline(client)  # type: ignore[arg-type]

                pipeline.search(
                    SearchRequest(
                        query="search value",
                        search_mode=search_mode,
                        page=2,
                        page_size=10,
                        publication_year=2020,
                        publication_year_end=2022,
                    )
                )

                self.assertEqual(client.calls, [
                    (
                        expected_method,
                        "search value",
                        {
                            "page": 2,
                            "page_size": 10,
                            "publication_year": 2020,
                            "publication_year_end": 2022,
                        },
                    )
                ])

    def test_returns_consistently_standardised_result(self) -> None:
        client = FakePubMedClient(
            self._page(
                fallback_used=True,
                invalid_record_count=1,
                records=(
                    self._record(
                        doi=" 10.1234/EXAMPLE ",
                        journal_title=" Example  Journal ",
                        abstract_available=True,
                    ),
                ),
            )
        )
        pipeline = SearchPipeline(client)  # type: ignore[arg-type]

        result = pipeline.search(
            SearchRequest(query="example", search_mode=SearchMode.TITLE)
        )

        self.assertEqual(result.source_name, "PubMed")
        self.assertEqual(result.total_count, 4)
        self.assertEqual(result.normalised_count, 1)
        self.assertEqual(result.page, 2)
        self.assertEqual(result.page_size, 10)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.invalid_record_count, 1)
        self.assertEqual(result.duplicate_record_count, 0)
        self.assertEqual(result.records[0].pmid, "12345")
        self.assertEqual(result.records[0].doi, "10.1234/example")
        self.assertEqual(result.records[0].title, "Example paper")
        self.assertEqual(result.records[0].authors, ("Ada Lovelace",))
        self.assertEqual(result.records[0].journal, "Example Journal")
        self.assertEqual(result.records[0].publication_year, 2024)
        self.assertTrue(result.records[0].abstract_available)

    def test_merges_duplicates_by_pmid_and_doi_through_partial_records(self) -> None:
        records = (
            self._record(pmid="123", doi="10.1234/example"),
            self._record(pmid="123", doi=None, journal_title="Recovered journal"),
            self._record(pmid="", doi="10.1234/EXAMPLE", authors=("Grace Hopper",)),
            self._record(pmid="456", doi=None, title="Different paper"),
        )
        client = FakePubMedClient(self._page(records=records))
        pipeline = SearchPipeline(client)  # type: ignore[arg-type]

        result = pipeline.search(
            SearchRequest(query="example", search_mode=SearchMode.TITLE)
        )

        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.normalised_count, 2)
        self.assertEqual(result.duplicate_record_count, 2)
        self.assertEqual(result.records[0].pmid, "123")
        self.assertEqual(result.records[0].doi, "10.1234/example")
        self.assertEqual(result.records[0].journal, "Example Journal")
        self.assertEqual(result.records[0].source_name, "PubMed")

    def test_strips_doi_url_and_doi_prefixes_before_deduplicating(self) -> None:
        records = (
            self._record(pmid="", doi="https://doi.org/10.1234/EXAMPLE"),
            self._record(pmid="", doi="doi:10.1234/example"),
        )
        client = FakePubMedClient(self._page(records=records))
        pipeline = SearchPipeline(client)  # type: ignore[arg-type]

        result = pipeline.search(
            SearchRequest(query="example", search_mode=SearchMode.TITLE)
        )

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].doi, "10.1234/example")
        self.assertEqual(result.duplicate_record_count, 1)

    def test_does_not_merge_records_with_different_pmids_sharing_a_doi(self) -> None:
        records = (
            self._record(pmid="123", doi="10.1234/example"),
            self._record(pmid="456", doi="10.1234/example", title="Erratum"),
            self._record(pmid="456", doi=None, journal_title="Erratum journal"),
        )
        client = FakePubMedClient(self._page(records=records))
        pipeline = SearchPipeline(client)  # type: ignore[arg-type]

        result = pipeline.search(
            SearchRequest(query="example", search_mode=SearchMode.TITLE)
        )

        self.assertEqual([record.pmid for record in result.records], ["123", "456"])
        self.assertEqual(result.duplicate_record_count, 1)
        self.assertEqual(result.records[1].title, "Erratum")

    def test_treats_zero_padded_pmids_as_the_same_paper(self) -> None:
        records = (
            self._record(pmid="123", doi=None),
            self._record(pmid="0123", doi=None),
        )
        client = FakePubMedClient(self._page(records=records))
        pipeline = SearchPipeline(client)  # type: ignore[arg-type]

        result = pipeline.search(
            SearchRequest(query="example", search_mode=SearchMode.TITLE)
        )

        self.assertEqual([record.pmid for record in result.records], ["123"])
        self.assertEqual(result.duplicate_record_count, 1)

    def test_keeps_unlinked_partial_identifiers_separate(self) -> None:
        client = FakePubMedClient(
            self._page(
                records=(
                    self._record(pmid="123", doi=None),
                    self._record(pmid="", doi="10.1234/example"),
                )
            )
        )
        pipeline = SearchPipeline(client)  # type: ignore[arg-type]

        result = pipeline.search(
            SearchRequest(query="example", search_mode=SearchMode.TITLE)
        )

        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.normalised_count, 2)
        self.assertEqual(result.duplicate_record_count, 0)

    def test_skips_invalid_records_without_losing_valid_records(self) -> None:
        client = FakePubMedClient(
            self._page(
                invalid_record_count=1,
                records=(self._record(), object()),
            )
        )
        pipeline = SearchPipeline(client)  # type: ignore[arg-type]

        result = pipeline.search(
            SearchRequest(query="example", search_mode=SearchMode.TITLE)
        )

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.normalised_count, 1)
        self.assertEqual(result.invalid_record_count, 2)

    def test_result_carries_pubmed_attribution_even_when_empty(self) -> None:
        for records in (None, ()):
            with self.subTest(records=records):
                client = FakePubMedClient(self._page(records=records))
                pipeline = SearchPipeline(client)  # type: ignore[arg-type]

                result = pipeline.search(
                    SearchRequest(query="example", search_mode=SearchMode.TITLE)
                )

                self.assertIs(result.attribution, PUBMED_ATTRIBUTION)
                self.assertEqual(result.source_name, "PubMed")
                for record in result.records:
                    self.assertEqual(record.source_name, "PubMed")

    def test_rejects_an_unsupported_search_mode(self) -> None:
        with self.assertRaises(SearchRequestValidationError):
            SearchRequest(query="example", search_mode="title")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
