"""Tests for the full-text services, using a fake PMC client and temp databases."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import tempfile
import unittest
from pathlib import Path

from hri import services
from hri.fulltext_services import build_attribution
from ingestion import PaperStore, StandardisedPaper
from ingestion.fulltext_store import FullTextStore
from ingestion.licence_policy import LicencePolicy
from ingestion.pmc_client import ArticleIds, CloudMetadata, PmcRequestValidationError


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pmc"
NOW = datetime(2026, 11, 17, 9, 30, tzinfo=timezone.utc)
CC_BY = "PMC10009402"
CC0 = "PMC12826829"


def metadata(version: str, **overrides) -> CloudMetadata:
    raw = json.loads((FIXTURES / f"{version}.json").read_text()) if (
        FIXTURES / f"{version}.json"
    ).exists() else {}
    values = dict(
        pmcid_version=version,
        pmid=str(raw["pmid"]) if raw.get("pmid") else None,
        doi=raw.get("doi"),
        title=raw.get("title"),
        citation=raw.get("citation"),
        licence=raw.get("license_code", "CC BY"),
        is_open_access=True,
        is_retracted=False,
        is_manuscript=False,
    )
    values.update(overrides)
    return CloudMetadata(**values)


class FakePmcClient:
    """Serve recorded articles; remember which XML was downloaded."""

    def __init__(self) -> None:
        self.search_results: tuple[str, ...] = (CC_BY, CC0)
        self.ids: dict[str, ArticleIds] = {
            CC_BY: ArticleIds(CC_BY, CC_BY, "35275515", "10.1017/S0033291721002701",
                              f"{CC_BY}.1"),
            CC0: ArticleIds(CC0, CC0, "41661935", "10.1371/journal.pmen.0000192",
                            f"{CC0}.1"),
        }
        self.metadata: dict[str, CloudMetadata | None] = {
            f"{CC_BY}.1": metadata(f"{CC_BY}.1"),
            f"{CC0}.1": metadata(f"{CC0}.1"),
        }
        self.xml: dict[str, bytes] = {
            f"{pmcid}.1": (FIXTURES / f"{pmcid}.1.xml").read_bytes()
            for pmcid in (CC_BY, CC0)
        }
        self.downloaded: list[str] = []
        self.converted: list[tuple[tuple[str, ...], str]] = []
        self.search_limits: list[int] = []

    def search_open_access(self, query, *, max_results=20):
        self.search_limits.append(max_results)
        if "OR" in query:
            raise PmcRequestValidationError("Plain-text searches must not contain ...")
        return self.search_results[:max_results]

    def convert_ids(self, ids, *, id_type):
        ids = tuple(ids)
        self.converted.append((ids, id_type))
        by_type = {
            "pmcid": {i.pmcid: i for i in self.ids.values()},
            "pmid": {i.pmid: i for i in self.ids.values()},
            "doi": {i.doi.lower(): i for i in self.ids.values() if i.doi},
        }[id_type]
        found = {}
        for value in ids:
            key = value.lower() if id_type == "doi" else value
            if key in by_type:
                found[value] = replace(by_type[key], requested_id=value)
        return found

    def cloud_metadata(self, version):
        return self.metadata.get(version)

    def article_xml(self, version):
        self.downloaded.append(version)
        return self.xml[version]


class FetchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "hri.sqlite3"
        self.client = FakePmcClient()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def fetch(self, **arguments):
        arguments.setdefault("client", self.client)
        arguments.setdefault("policy", LicencePolicy.of(["CC0", "CC BY"]))
        arguments.setdefault("now", lambda: NOW)
        if "from_db" not in arguments:
            arguments.setdefault("query", "adhd employment")
        return services.fetch_fulltext(database_path=self.db, **arguments)


class FetchByQueryTests(FetchTestCase):
    def test_stores_allowed_articles_with_provenance_and_chunks(self) -> None:
        result = self.fetch()

        self.assertEqual((result.source, result.query), ("query", "adhd employment"))
        self.assertEqual((result.candidates, result.stored, result.already_stored),
                         (2, 2, 0))
        self.assertEqual((result.rejected_licence, result.not_in_open_access,
                          result.failed, result.rejections), (0, 0, 0, ()))
        store = FullTextStore(self.db)
        document = store.get(CC_BY)
        self.assertEqual(
            (document.pmcid_version, document.pmid, document.doi, document.licence,
             document.retrieved_at),
            (f"{CC_BY}.1", "35275515", "10.1017/s0033291721002701", "CC BY",
             "2026-11-17T09:30:00Z"),
        )
        self.assertEqual(document.source_url,
                         f"https://pmc-oa-opendata.s3.amazonaws.com/{CC_BY}.1/{CC_BY}.1.xml")
        for expected in ("Magnus Helgesson", "Psychol Med", "Licence: CC BY "
                         "(https://creativecommons.org/licenses/by/4.0/)",
                         f"https://pmc.ncbi.nlm.nih.gov/articles/{CC_BY}/",
                         "retrieved 2026-11-17", "references, figures, and tables removed"):
            with self.subTest(attribution=expected):
                self.assertIn(expected, document.attribution)
        chunks = store.chunks(CC_BY)
        self.assertEqual(chunks[0].section_name, "title")
        self.assertIn("abstract", {c.section_name for c in chunks})
        self.assertTrue(all(c.licence == "CC BY" for c in chunks))
        self.assertEqual(store.get(CC0).licence, "CC0")

    def test_rejects_disallowed_licences_without_downloading_text(self) -> None:
        self.client.metadata[f"{CC0}.1"] = metadata(f"{CC0}.1", licence="CC BY-NC")
        self.client.metadata[f"{CC_BY}.1"] = metadata(f"{CC_BY}.1", licence=None)

        with self.assertLogs("hri.fulltext_services", "WARNING") as logs:
            result = self.fetch()

        self.assertEqual((result.stored, result.rejected_licence), (0, 2))
        self.assertEqual(self.client.downloaded, [])
        self.assertEqual(
            sorted((r.pmcid, r.licence, r.reason) for r in result.rejections),
            [(CC_BY, None, "licence"), (CC0, "CC BY-NC", "licence")],
        )
        self.assertEqual(FullTextStore(self.db).stats().documents, 0)
        self.assertTrue(any(f"{CC0}: licence" in line for line in logs.output))
        self.assertFalse(any("CC BY-NC" in line for line in logs.output))

    def test_a_wider_policy_allows_more_licences(self) -> None:
        self.client.metadata[f"{CC0}.1"] = metadata(f"{CC0}.1", licence="CC BY-NC")

        result = self.fetch(policy=LicencePolicy.of(["CC BY", "CC BY-NC"]))

        self.assertEqual((result.stored, result.rejected_licence), (2, 0))
        self.assertEqual(FullTextStore(self.db).get(CC0).licence, "CC BY-NC")

    def test_reads_the_licence_policy_from_the_environment_by_default(self) -> None:
        from unittest import mock

        self.client.metadata[f"{CC0}.1"] = metadata(f"{CC0}.1", licence="CC BY-SA")
        with mock.patch.dict("os.environ", {"HRI_ALLOWED_LICENCES": "CC BY-SA"}):
            result = services.fetch_fulltext(
                query="x", database_path=self.db, client=self.client, now=lambda: NOW
            )

        self.assertEqual((result.stored, result.rejected_licence), (1, 1))

    def test_rejects_retracted_not_open_access_and_missing_versions(self) -> None:
        self.client.ids["PMC3"] = ArticleIds("PMC3", "PMC3", None, None, None)
        self.client.ids["PMC4"] = ArticleIds("PMC4", "PMC4", None, None, "PMC4.2")
        self.client.ids["PMC5"] = ArticleIds("PMC5", "PMC5", None, None, "PMC5.1")
        self.client.metadata["PMC5.1"] = metadata("PMC5.1", is_open_access=False)
        self.client.metadata[f"{CC_BY}.1"] = metadata(f"{CC_BY}.1", is_retracted=True)
        self.client.search_results = (CC_BY, "PMC3", "PMC4", "PMC5", CC0)

        result = self.fetch()

        self.assertEqual(
            (result.stored, result.rejected_retracted, result.not_in_open_access,
             result.current_version_unavailable),
            (1, 1, 3, 2),
        )
        self.assertEqual(
            {r.pmcid: r.reason for r in result.rejections},
            {CC_BY: "retracted", "PMC3": "current_version_unavailable",
             "PMC4": "current_version_unavailable", "PMC5": "not_open_access"},
        )
        self.assertEqual(self.client.downloaded, [f"{CC0}.1"])

    def test_rejects_articles_whose_xml_links_to_a_retraction(self) -> None:
        xml = self.client.xml[f"{CC0}.1"].replace(
            b"<abstract>",
            b'<abstract><p><related-article related-article-type="retraction-forward"/>'
            b"Retracted.</p>",
        )
        self.client.xml[f"{CC0}.1"] = xml

        result = self.fetch()

        self.assertEqual((result.stored, result.rejected_retracted), (1, 1))
        self.assertIsNone(FullTextStore(self.db).get(CC0))

    def test_flags_corrected_articles_but_stores_them(self) -> None:
        self.client.xml[f"{CC0}.1"] = self.client.xml[f"{CC0}.1"].replace(
            b"<abstract>",
            b'<abstract><p><related-article related-article-type="correction-forward"/>'
            b"Corrected.</p>",
        )

        result = self.fetch()

        self.assertEqual((result.stored, result.flagged), (2, 1))
        self.assertEqual(FullTextStore(self.db).get(CC0).notice_flags, ("corrected",))

    def test_counts_unparseable_articles_as_failed_and_stores_nothing(self) -> None:
        self.client.xml[f"{CC0}.1"] = b"<article><body>"

        with self.assertLogs("hri.fulltext_services", "WARNING"):
            result = self.fetch()

        self.assertEqual((result.stored, result.failed, result.rejections), (1, 1, ()))

    def test_skips_versions_already_stored_and_respects_the_limit(self) -> None:
        self.fetch(max_articles=1)
        self.assertEqual(FullTextStore(self.db).stats().documents, 1)

        second = self.fetch(max_articles=1)

        self.assertEqual((second.already_stored, second.stored, second.candidates),
                         (1, 1, 2))
        self.assertEqual(self.client.downloaded, [f"{CC_BY}.1", f"{CC0}.1"])
        self.assertEqual(self.client.search_limits, [200, 200])

    def test_rejects_invalid_requests(self) -> None:
        for arguments in (
            {"query": None},
            {"query": "x", "from_db": True},
            {"query": "x", "max_articles": 0},
            {"query": "x", "max_articles": 201},
            {"query": "heart OR lung"},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(services.InvalidRequestError):
                    self.fetch(**arguments)


class AttributionTests(unittest.TestCase):
    def test_builds_a_credit_line_with_licence_source_and_changes(self) -> None:
        text = build_attribution(
            authors=[f"Author {n}" for n in range(1, 8)],
            title="Does it work?",
            citation="J Test. 2024;1(2):3-4.",
            pmcid_version="PMC5.2",
            licence="CC BY",
            licence_url="https://creativecommons.org/licenses/by/4.0/",
            retrieved_at="2026-11-17T09:30:00Z",
        )

        self.assertEqual(
            text,
            "Author 1, Author 2, Author 3, Author 4, Author 5, Author 6, et al. "
            "Does it work? J Test. 2024;1(2):3-4. PMC5.2. Licence: CC BY "
            "(https://creativecommons.org/licenses/by/4.0/). Source: PubMed "
            "Central, https://pmc.ncbi.nlm.nih.gov/articles/PMC5/, retrieved "
            "2026-11-17. Changes: text extracted and split into passages; "
            "references, figures, and tables removed.",
        )

    def test_leaves_out_missing_authors_and_citation(self) -> None:
        text = build_attribution(
            authors=[], title="Title.", citation=None, pmcid_version="PMC5.1",
            licence="CC0", licence_url=None, retrieved_at="2026-11-17T00:00:00Z",
        )

        self.assertTrue(text.startswith("Title. PMC5.1. Licence: CC0. Source:"))


class FetchFromDatabaseTests(FetchTestCase):
    def setUp(self) -> None:
        super().setUp()
        papers = PaperStore(self.db)
        base = StandardisedPaper(
            pmid=None, doi=None, title="Paper", authors=(), journal=None,
            publication_year=2024, abstract_available=True, source_identifier=None,
            source_url=None, source_name="PubMed",
        )
        papers.save(replace(base, pmid="35275515"))
        papers.save(replace(base, doi="10.1371/journal.pmen.0000192"))
        papers.save(replace(base, pmid="999"))  # in PubMed, not in PMC

    def test_fetches_full_text_for_stored_papers_and_links_them(self) -> None:
        result = self.fetch(from_db=True)

        self.assertEqual((result.source, result.query), ("database", None))
        self.assertEqual((result.candidates, result.stored, result.not_in_pmc), (2, 2, 1))
        self.assertEqual([call[1] for call in self.client.converted], ["pmid", "doi"])
        store = FullTextStore(self.db)
        for pmcid in (CC_BY, CC0):
            with self.subTest(pmcid=pmcid):
                self.assertIsNotNone(store.linked_paper(store.get(pmcid)))

    def test_never_copies_a_pmc_abstract_onto_the_citation_record(self) -> None:
        self.fetch(from_db=True)

        paper = PaperStore(self.db).get(pmid="35275515")
        self.assertFalse(hasattr(paper, "abstract"))
        self.assertEqual(paper.title, "Paper")


class RecheckTests(FetchTestCase):
    def test_removes_retracted_articles_and_reports_changes(self) -> None:
        self.fetch()
        self.client.metadata[f"{CC_BY}.1"] = metadata(f"{CC_BY}.1", is_retracted=True)
        self.client.ids[CC0] = replace(self.client.ids[CC0], current_version=f"{CC0}.2")
        self.client.metadata[f"{CC0}.2"] = metadata(f"{CC0}.2")

        with self.assertLogs("hri.fulltext_services", "WARNING"):
            result = services.recheck_fulltext(
                database_path=self.db, client=self.client, now=lambda: NOW
            )

        self.assertEqual(result.checked, 2)
        self.assertEqual(result.removed_retracted, (CC_BY,))
        self.assertEqual(result.newer_version_available, (CC0,))
        self.assertEqual(result.unavailable, ())
        store = FullTextStore(self.db)
        self.assertIsNone(store.get(CC_BY))
        self.assertEqual(store.chunks(CC_BY), ())
        self.assertEqual(store.rejections()[0].reason, "retracted")

    def test_a_retracted_current_version_removes_the_stored_one(self) -> None:
        self.fetch()
        self.client.ids[CC0] = replace(self.client.ids[CC0], current_version=f"{CC0}.2")
        self.client.metadata[f"{CC0}.2"] = metadata(f"{CC0}.2", is_retracted=True)
        self.client.metadata[f"{CC_BY}.1"] = None

        with self.assertLogs("hri.fulltext_services", "WARNING"):
            result = services.recheck_fulltext(
                database_path=self.db, client=self.client, now=lambda: NOW
            )

        self.assertEqual(result.removed_retracted, (CC0,))
        self.assertEqual(result.unavailable, (CC_BY,))
        self.assertIsNotNone(FullTextStore(self.db).get(CC_BY))

    def test_stats_report_documents_chunks_and_rejections(self) -> None:
        self.client.metadata[f"{CC0}.1"] = metadata(f"{CC0}.1", licence="CC BY-ND")
        self.fetch()

        stats = services.get_fulltext_stats(database_path=self.db)

        self.assertEqual(stats.documents, 1)
        self.assertGreater(stats.chunks, 1)
        self.assertEqual(stats.rejections_by_reason, (("licence", 1),))


if __name__ == "__main__":
    unittest.main()
