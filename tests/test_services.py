"""Tests for the service layer, using a fake PubMed client and temporary databases."""

from __future__ import annotations

import csv
from datetime import date
from io import StringIO
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hri import services
from hri.config import DEFAULT_DATABASE_PATH, resolve_database_path
from ingestion import (
    PUBMED_ATTRIBUTION,
    PaperStore,
    PubMedConfigurationError,
    PubMedTransportError,
    SearchMode,
    StandardisedPaper,
)
from ingestion.metadata import PaperMetadata
from ingestion.pubmed import PubMedSearchPage, PubMedSearchRecord


def _record(pmid: str, *, doi: str | None = None, title: str | None = None):
    return PubMedSearchRecord(
        metadata=PaperMetadata(
            title=title or f"Paper {pmid}",
            authors=("Ada Lovelace", "Grace Hopper"),
            publication_year=2020 + int(pmid) % 3,
            source_identifier=f"PMID:{pmid}",
            source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        ),
        pmid=pmid,
        doi=doi,
        journal_title="Example Journal",
        citation_date="2020",
        abstract_available=int(pmid) % 2 == 0,
    )


class FakePubMedClient:
    """Serve prepared records page by page and remember every call."""

    def __init__(self, records, *, total_count=None, invalid_per_page=0, error=None):
        self.records = list(records)
        self.total_count = len(self.records) if total_count is None else total_count
        self.invalid_per_page = invalid_per_page
        self.error = error
        self.calls: list[tuple[str, str, dict]] = []

    def _search(self, method, query, arguments):
        self.calls.append((method, query, arguments))
        if self.error is not None:
            raise self.error
        start = (arguments["page"] - 1) * arguments["page_size"]
        return PubMedSearchPage(
            records=tuple(self.records[start : start + arguments["page_size"]]),
            total_count=self.total_count,
            page=arguments["page"],
            page_size=arguments["page_size"],
            search_mode=SearchMode.TITLE_ABSTRACT,
            fallback_used=False,
            invalid_record_count=self.invalid_per_page,
        )

    def search_title(self, query, **arguments):
        return self._search("search_title", query, arguments)

    def search_title_abstract(self, query, **arguments):
        return self._search("search_title_abstract", query, arguments)

    def search_author(self, query, **arguments):
        return self._search("search_author", query, arguments)

    def lookup_doi(self, query, **arguments):
        return self._search("lookup_doi", query, arguments)

    def lookup_pmid(self, query, **arguments):
        return self._search("lookup_pmid", query, arguments)


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "hri.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _ingest(self, records, **arguments):
        client = arguments.pop("client", None) or FakePubMedClient(records)
        result = services.ingest(
            arguments.pop("query", "sleep"),
            database_path=self.db, client=client, **arguments,
        )
        return result, client


class ContractDocumentTests(unittest.TestCase):
    def test_every_public_service_name_is_documented(self) -> None:
        document = (
            Path(__file__).resolve().parent.parent / "docs" / "SERVICE_API.md"
        ).read_text()
        for name in services.__all__:
            with self.subTest(name=name):
                self.assertIn(f"`{name}", document)
        for name in services.__all__:
            if name[0].islower():  # functions each need their own section
                with self.subTest(section=name):
                    self.assertIn(f"### `{name}`", document)


class ConfigTests(unittest.TestCase):
    def test_explicit_path_wins_then_environment_then_default(self) -> None:
        environment = {"HRI_DB_PATH": "/tmp/from-env.sqlite3"}
        self.assertEqual(
            resolve_database_path("/tmp/explicit.sqlite3", environment),
            Path("/tmp/explicit.sqlite3"),
        )
        self.assertEqual(
            resolve_database_path(None, environment), Path("/tmp/from-env.sqlite3")
        )
        for blank in ({}, {"HRI_DB_PATH": "  "}):
            self.assertEqual(resolve_database_path(None, blank), DEFAULT_DATABASE_PATH)
        self.assertEqual(DEFAULT_DATABASE_PATH, Path("data/hri.sqlite3"))


class IngestTests(ServiceTestCase):
    def test_fetches_pages_until_max_and_saves_with_attribution(self) -> None:
        records = [_record(str(number)) for number in range(1, 61)]

        result, client = self._ingest(records, max_records=45)

        self.assertEqual(
            (result.fetched, result.saved, result.duplicates,
             result.invalid_skipped, result.conflicts),
            (45, 45, 0, 0, 0),
        )
        self.assertEqual(result.total_available, 60)
        self.assertEqual(result.requested_max, 45)
        self.assertIs(result.attribution, PUBMED_ATTRIBUTION)
        self.assertEqual([call[2]["page"] for call in client.calls], [1, 2, 3])
        self.assertEqual({call[2]["page_size"] for call in client.calls}, {20})
        stored = PaperStore(self.db)
        self.assertEqual(stored.stats().total_papers, 45)
        self.assertIs(stored.get(pmid="1").attribution, PUBMED_ATTRIBUTION)

    def test_stops_when_pubmed_has_fewer_records_than_requested(self) -> None:
        result, client = self._ingest([_record("1"), _record("2")], max_records=200)

        self.assertEqual((result.fetched, result.saved), (2, 2))
        self.assertEqual(len(client.calls), 1)

    def test_no_matches_saves_nothing(self) -> None:
        result, client = self._ingest([])

        self.assertEqual((result.fetched, result.saved, result.total_available), (0, 0, 0))
        self.assertEqual(len(client.calls), 1)

    def test_counts_invalid_duplicates_and_conflicts_and_keeps_going(self) -> None:
        PaperStore(self.db).save(StandardisedPaper(
            pmid="900", doi="10.1234/taken", title="Existing", authors=(),
            journal=None, publication_year=2020, abstract_available=False,
            source_identifier=None, source_url=None, source_name="PubMed",
        ))
        records = [
            _record("1"),
            _record("1"),                         # same page duplicate
            _record("2", doi="10.1234/taken"),    # DOI belongs to PMID 900
            _record("3"),
        ]
        client = FakePubMedClient(records, invalid_per_page=2)

        result, _ = self._ingest(records, client=client)

        self.assertEqual(
            (result.fetched, result.saved, result.duplicates,
             result.invalid_skipped, result.conflicts),
            (3, 2, 1, 2, 1),
        )
        self.assertIsNone(PaperStore(self.db).get(pmid="2"))

    def test_counts_a_paper_repeated_on_a_later_page_as_duplicate(self) -> None:
        records = [_record(str(number)) for number in range(1, 21)] + [_record("5")]

        result, _ = self._ingest(records)

        self.assertEqual((result.fetched, result.saved, result.duplicates), (21, 20, 1))

    def test_reads_at_most_max_positions_when_records_are_unusable(self) -> None:
        client = FakePubMedClient([], total_count=5000, invalid_per_page=20)

        result, _ = self._ingest([], client=client, max_records=40)

        self.assertEqual(len(client.calls), 2)
        self.assertEqual((result.fetched, result.invalid_skipped), (0, 40))

    def test_routes_mode_and_maps_open_ended_year_ranges(self) -> None:
        next_year = date.today().year + 1
        cases = (
            ({"mode": "author"}, "search_author", None, None),
            ({"mode": SearchMode.TITLE, "year_from": 2018}, "search_title", 2018, next_year),
            ({"year_to": 2010}, "search_title_abstract", 1500, 2010),
            ({"year_from": 2015, "year_to": 2016}, "search_title_abstract", 2015, 2016),
        )
        for arguments, method, start, end in cases:
            with self.subTest(arguments=arguments):
                _, client = self._ingest([_record("1")], **arguments)
                called, query, sent = client.calls[0]
                self.assertEqual((called, query), (method, "sleep"))
                self.assertEqual(
                    (sent["publication_year"], sent["publication_year_end"]),
                    (start, end),
                )

    def test_rejects_invalid_arguments_before_any_request(self) -> None:
        invalid = (
            {"query": "  "},
            {"mode": "fulltext"},
            {"max_records": 0},
            {"max_records": 1001},
            {"max_records": True},
            {"year_from": "2018"},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                client = FakePubMedClient([_record("1")])
                with self.assertRaises(services.InvalidRequestError):
                    self._ingest([], client=client, **arguments)
                self.assertEqual(client.calls, [])

    def test_reports_client_validation_failures_as_invalid_requests(self) -> None:
        from ingestion import PubMedRequestValidationError

        client = FakePubMedClient(
            [], error=PubMedRequestValidationError("Plain-text searches ...")
        )
        with self.assertRaises(services.InvalidRequestError):
            self._ingest([], client=client, query="heart OR lung")

    def test_pubmed_failures_propagate_and_keep_saved_papers(self) -> None:
        client = FakePubMedClient([], error=PubMedTransportError("offline"))
        with self.assertRaises(PubMedTransportError):
            self._ingest([], client=client)

    def test_builds_the_live_client_from_environment_only_when_not_injected(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(PubMedConfigurationError):
                services.ingest("sleep", database_path=self.db)


class SearchAndReadTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._ingest([
            _record("1", title="Sleep and memory"),
            _record("2", title="Sleep in adults", doi="10.1234/two"),
            _record("3", title="Diet and exercise"),
        ])

    def test_search_returns_a_page_with_totals_and_attribution(self) -> None:
        results = services.search_papers(
            "sleep", page=2, page_size=1, database_path=self.db
        )

        self.assertEqual([paper.pmid for paper in results.papers], ["2"])
        self.assertEqual(
            (results.total_count, results.page, results.page_size, results.total_pages),
            (2, 2, 1, 2),
        )
        self.assertEqual((results.query, results.strategy), ("sleep", "keyword"))
        self.assertIs(results.attribution, PUBMED_ATTRIBUTION)
        self.assertEqual(
            results.papers[0].pubmed_url, "https://pubmed.ncbi.nlm.nih.gov/2/"
        )

    def test_search_supports_strategies_and_filters(self) -> None:
        boolean = services.search_papers(
            "sleep AND NOT adults", strategy="boolean", database_path=self.db
        )
        fuzzy = services.search_papers("slep", strategy="fuzzy", database_path=self.db)
        filtered = services.search_papers(
            "sleep", year_from=2022, abstract_available=True, database_path=self.db
        )

        self.assertEqual([paper.pmid for paper in boolean.papers], ["1"])
        self.assertEqual(fuzzy.total_count, 2)
        self.assertEqual([paper.pmid for paper in filtered.papers], ["2"])

    def test_search_without_matches_has_no_pages_or_attribution(self) -> None:
        results = services.search_papers("nothing", database_path=self.db)

        self.assertEqual((results.papers, results.total_count, results.total_pages), ((), 0, 0))
        self.assertIsNone(results.attribution)

    def test_search_rejects_invalid_arguments(self) -> None:
        invalid = (
            {"query": " "},
            {"strategy": "semantic"},
            {"page": 0},
            {"page_size": 101},
            {"year_from": 2025, "year_to": 2020},
            {"query": "sleep AND", "strategy": "boolean"},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                arguments = {"query": "sleep", **arguments}
                with self.assertRaises(services.InvalidRequestError):
                    services.search_papers(
                        arguments.pop("query"), database_path=self.db, **arguments
                    )

    def test_get_paper_accepts_a_pmid_or_a_doi(self) -> None:
        by_pmid = services.get_paper(" 2 ", database_path=self.db)
        by_doi = services.get_paper("https://doi.org/10.1234/TWO", database_path=self.db)

        self.assertEqual(by_pmid, by_doi)
        self.assertEqual(by_pmid.title, "Sleep in adults")
        self.assertIs(by_pmid.attribution, PUBMED_ATTRIBUTION)

    def test_get_paper_reports_missing_and_invalid_identifiers(self) -> None:
        with self.assertRaises(services.PaperNotFoundError):
            services.get_paper("999", database_path=self.db)
        for identifier in ("", "not-an-id", None):
            with self.subTest(identifier=identifier):
                with self.assertRaises(services.InvalidRequestError):
                    services.get_paper(identifier, database_path=self.db)

    def test_get_stats_counts_the_store(self) -> None:
        stats = services.get_stats(database_path=self.db)

        self.assertEqual(stats.total_papers, 3)
        self.assertEqual((stats.with_doi, stats.without_doi), (1, 2))
        self.assertEqual(sum(count for _, count in stats.papers_by_year), 3)

    def test_database_path_comes_from_the_environment_by_default(self) -> None:
        with mock.patch.dict("os.environ", {"HRI_DB_PATH": str(self.db)}):
            self.assertEqual(services.get_stats().total_papers, 3)


class SavedListServiceTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._ingest([
            _record("1", title="=Risky title"),
            _record("2", title="Sleep in adults.", doi="10.1234/two"),
        ])

    def test_create_add_remove_and_read_lists(self) -> None:
        created = services.create_list("Reading", database_path=self.db)
        added = services.add_to_list("Reading", "1", database_path=self.db)
        again = services.add_to_list("reading", "1", database_path=self.db)
        services.add_to_list("Reading", "10.1234/two", database_path=self.db)

        self.assertEqual((created.name, created.paper_count), ("Reading", 0))
        self.assertEqual((added.changed, added.saved_list.paper_count), (True, 1))
        self.assertEqual(added.paper.pmid, "1")
        self.assertFalse(again.changed)

        contents = services.get_list("Reading", database_path=self.db)
        self.assertEqual([paper.pmid for paper in contents.papers], ["1", "2"])
        self.assertIs(contents.attribution, PUBMED_ATTRIBUTION)
        self.assertEqual(
            [(saved.name, saved.paper_count)
             for saved in services.get_lists(database_path=self.db)],
            [("Reading", 2)],
        )

        removed = services.remove_from_list("Reading", "1", database_path=self.db)
        missing = services.remove_from_list("Reading", "999", database_path=self.db)
        self.assertEqual((removed.changed, removed.saved_list.paper_count), (True, 1))
        self.assertEqual((missing.changed, missing.paper), (False, None))

    def test_list_errors_are_specific(self) -> None:
        services.create_list("Reading", database_path=self.db)
        with self.assertRaises(services.SavedListExistsError):
            services.create_list("READING", database_path=self.db)
        with self.assertRaises(services.SavedListNotFoundError):
            services.get_list("Missing", database_path=self.db)
        with self.assertRaises(services.SavedListNotFoundError):
            services.export_list("Missing", database_path=self.db)
        with self.assertRaises(services.PaperNotFoundError):
            services.add_to_list("Reading", "999", database_path=self.db)
        for action in (
            lambda: services.create_list("  ", database_path=self.db),
            lambda: services.add_to_list("Reading", "bad id", database_path=self.db),
            lambda: services.export_list("Reading", "pdf", database_path=self.db),
        ):
            with self.assertRaises(services.InvalidRequestError):
                action()

    def test_exports_csv_with_safe_cells(self) -> None:
        services.create_list("Reading", database_path=self.db)
        services.add_to_list("Reading", "1", database_path=self.db)
        services.add_to_list("Reading", "2", database_path=self.db)

        export = services.export_list("Reading", "csv", database_path=self.db)

        rows = list(csv.DictReader(StringIO(export.content)))
        self.assertEqual((export.list_name, export.export_format), ("Reading", "csv"))
        self.assertEqual(export.paper_count, 2)
        self.assertIs(export.attribution, PUBMED_ATTRIBUTION)
        self.assertEqual(rows[0]["title"], "'=Risky title")
        self.assertEqual(rows[1], {
            "pmid": "2", "doi": "10.1234/two", "title": "Sleep in adults.",
            "authors": "Ada Lovelace; Grace Hopper", "journal": "Example Journal",
            "publication_year": "2022", "volume": "", "issue": "", "pages": "",
            "abstract_available": "yes",
            "source": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/2/",
        })

    def test_exports_vancouver_and_plain_citations_with_attribution(self) -> None:
        services.create_list("Reading", database_path=self.db)
        services.add_to_list("Reading", "2", database_path=self.db)

        vancouver = services.export_list("Reading", "citations", database_path=self.db)
        export = services.export_list("Reading", "plain", database_path=self.db)

        self.assertEqual(
            vancouver.content.splitlines()[0],
            "1. Ada Lovelace, Grace Hopper. Sleep in adults. Example Journal. 2022. "
            "doi: 10.1234/two. PMID: 2.",
        )
        self.assertIn(PUBMED_ATTRIBUTION.source_name, vancouver.content.splitlines())
        self.assertEqual(export.export_format, "plain")
        lines = export.content.splitlines()
        self.assertEqual(
            lines[0],
            "1. Ada Lovelace, Grace Hopper. Sleep in adults. Example Journal. 2022. "
            "PMID: 2. doi:10.1234/two. https://pubmed.ncbi.nlm.nih.gov/2/",
        )
        self.assertIn(PUBMED_ATTRIBUTION.source_name, lines)
        self.assertIn(PUBMED_ATTRIBUTION.independent_service_notice, lines)
        self.assertIn(f"Disclaimer: {PUBMED_ATTRIBUTION.disclaimer_url}", lines)

    def test_exports_an_empty_list(self) -> None:
        services.create_list("Empty", database_path=self.db)

        csv_export = services.export_list("Empty", "csv", database_path=self.db)
        self.assertEqual(len(csv_export.content.splitlines()), 1)
        for export_format in ("citations", "plain"):
            text_export = services.export_list(
                "Empty", export_format, database_path=self.db
            )
            self.assertEqual((text_export.content, text_export.attribution), ("", None))


if __name__ == "__main__":
    unittest.main()
