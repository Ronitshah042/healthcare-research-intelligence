"""Tests for storing open-access full text and rejections in temporary databases."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from ingestion.chunking import chunk_sections
from ingestion.fulltext import ArticleSection, parse_article
from ingestion.fulltext_store import FullTextDocument, FullTextStore
from ingestion.pipeline import StandardisedPaper
from ingestion.storage import PaperStore


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pmc"
DOCUMENT = FullTextDocument(
    pmcid="PMC10009402",
    pmcid_version="PMC10009402.1",
    pmid="35275515",
    doi="10.1017/S0033291721002701",
    title="Labour market marginalisation in young adults diagnosed with ADHD",
    licence="CC BY",
    source_url="https://pmc-oa-opendata.s3.amazonaws.com/PMC10009402.1/PMC10009402.1.xml",
    retrieved_at="2026-11-17T09:30:00Z",
    attribution="Psychol Med. 2023. Licence: CC BY. Source: PMC10009402.1.",
    article_type="research-article",
    notice_flags=(),
)
CVD_DOCUMENT = replace(
    DOCUMENT,
    pmcid="PMC12826829",
    pmcid_version="PMC12826829.1",
    pmid=None,
    doi=None,
    title="Cardiovascular disease in older U.S. military veterans",
    licence="CC0",
    attribution="Licence: CC0. Source: PMC12826829.1.",
)


class FullTextStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "hri.sqlite3"
        self.store = FullTextStore(self.path)
        article = parse_article((FIXTURES / "PMC10009402.1.xml").read_bytes())
        self.sections = article.sections
        self.chunks = chunk_sections(article.sections)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_saves_and_reads_a_document_with_sections_and_chunks(self) -> None:
        saved = self.store.save(DOCUMENT, self.sections, self.chunks)

        self.assertEqual(saved.doi, "10.1017/s0033291721002701")
        self.assertEqual(self.store.get("pmc10009402"), saved)
        self.assertEqual(self.store.documents(), (saved,))
        self.assertEqual(self.store.sections("PMC10009402"), self.sections)
        chunks = self.store.chunks("PMC10009402")
        self.assertEqual(len(chunks), len(self.chunks))
        self.assertEqual(
            [(c.position, c.section_name, c.text) for c in chunks],
            [(c.position, c.section_name, c.text) for c in self.chunks],
        )
        self.assertTrue(all(c.licence == "CC BY" for c in chunks))
        self.assertTrue(all(c.pmcid_version == "PMC10009402.1" for c in chunks))

    def test_saving_again_replaces_the_previous_version(self) -> None:
        self.store.save(DOCUMENT, self.sections, self.chunks)
        newer = replace(
            DOCUMENT, pmcid_version="PMC10009402.2", notice_flags=("corrected",)
        )
        one_section = (ArticleSection("abstract", None, ("Short.",)),)

        self.store.save(newer, one_section, chunk_sections(one_section))

        self.assertEqual(self.store.get("PMC10009402").pmcid_version, "PMC10009402.2")
        self.assertEqual(self.store.get("PMC10009402").notice_flags, ("corrected",))
        self.assertEqual([c.text for c in self.store.chunks("PMC10009402")], ["Short."])
        self.assertEqual(self.store.stats().documents, 1)

    def test_rejecting_deletes_stored_text_and_saving_clears_the_rejection(self) -> None:
        self.store.save(DOCUMENT, self.sections, self.chunks)

        rejection = self.store.reject(
            "PMC10009402", "retracted", recorded_at="2026-11-18T00:00:00Z",
            pmcid_version="PMC10009402.1", licence="CC BY",
        )

        self.assertEqual(rejection.reason, "retracted")
        self.assertIsNone(self.store.get("PMC10009402"))
        self.assertEqual(self.store.chunks("PMC10009402"), ())
        self.assertEqual(self.store.sections("PMC10009402"), ())
        with closing(sqlite3.connect(self.path)) as connection:
            for table in ("documents", "document_sections", "chunks"):
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                self.assertEqual(count, 0, table)

        self.store.save(DOCUMENT, self.sections, self.chunks)
        self.assertEqual(self.store.rejections(), ())

    def test_rejections_keep_only_the_latest_record_per_article(self) -> None:
        self.store.reject("PMC1", "licence", recorded_at="2026-11-17T01:00:00Z",
                          licence="CC BY-NC")
        self.store.reject("PMC1", "licence", recorded_at="2026-11-17T02:00:00Z",
                          licence="CC BY-ND")
        self.store.reject("PMC2", "not_open_access", recorded_at="2026-11-17T03:00:00Z")

        rejections = self.store.rejections()

        self.assertEqual([(r.pmcid, r.licence) for r in rejections],
                         [("PMC2", None), ("PMC1", "CC BY-ND")])
        self.assertEqual(
            self.store.stats().rejections_by_reason,
            (("licence", 1), ("not_open_access", 1)),
        )
        with self.assertRaises(ValueError):
            self.store.reject("PMC3", "too long", recorded_at="2026-11-17T00:00:00Z")

    def test_finds_documents_by_pmid_or_doi_and_links_stored_papers(self) -> None:
        saved = self.store.save(DOCUMENT, self.sections, self.chunks)

        self.assertEqual(self.store.find(pmid="035275515"), saved)
        self.assertEqual(self.store.find(doi="doi:10.1017/S0033291721002701"), saved)
        self.assertIsNone(self.store.linked_paper(saved))

        papers = PaperStore(self.path)
        paper = StandardisedPaper(
            pmid=None, doi="10.1017/S0033291721002701", title="Labour market",
            authors=(), journal=None, publication_year=2023, abstract_available=True,
            source_identifier=None, source_url=None, source_name="PubMed",
        )
        papers.save(paper)
        self.assertEqual(self.store.linked_paper(saved).title, "Labour market")
        papers.save(replace(paper, pmid="35275515", title="By PMID"))
        self.assertEqual(self.store.linked_paper(saved).title, "By PMID")
        with self.assertRaises(ValueError):
            self.store.find()

    def test_stats_count_documents_chunks_and_flags(self) -> None:
        self.store.save(DOCUMENT, self.sections, self.chunks)
        self.store.save(
            replace(DOCUMENT, pmcid="PMC5", pmcid_version="PMC5.1", pmid=None,
                    doi=None, notice_flags=("expression_of_concern",)),
            self.sections[:1], chunk_sections(self.sections[:1]),
        )

        stats = self.store.stats()

        self.assertEqual((stats.documents, stats.flagged_documents), (2, 1))
        self.assertEqual(stats.chunks, len(self.chunks) + 1)

    def test_rejects_documents_missing_provenance(self) -> None:
        for bad in (
            replace(DOCUMENT, licence=" "),
            replace(DOCUMENT, attribution=""),
            replace(DOCUMENT, pmcid_version="PMC999.1"),
            replace(DOCUMENT, pmcid="12abc"),
        ):
            with self.subTest(document=bad):
                with self.assertRaises(ValueError):
                    self.store.save(bad, self.sections, self.chunks)
        self.assertEqual(self.store.stats().documents, 0)


class PassageSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = FullTextStore(Path(self.temp_dir.name) / "hri.sqlite3")
        for document in (DOCUMENT, CVD_DOCUMENT):
            name = document.pmcid_version + ".xml"
            article = parse_article((FIXTURES / name).read_bytes())
            self.store.save(document, article.sections, chunk_sections(article.sections))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_finds_passages_with_every_word_best_first(self) -> None:
        matches = self.store.search("ADHD Sweden", limit=20)

        self.assertTrue(matches)
        for match in matches:
            self.assertEqual(match.document.pmcid, "PMC10009402")
            self.assertEqual(match.chunk.licence, "CC BY")
            self.assertEqual(match.document.attribution, DOCUMENT.attribution)
            text = match.chunk.text.casefold()
            self.assertIn("adhd", text)
            self.assertIn("swed", text)
        scores = [match.score for match in matches]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(score > 0 for score in scores))

    def test_needs_every_word_and_stems_them(self) -> None:
        self.assertEqual(self.store.search("ADHD veterans", limit=20), ())
        plural = self.store.search("veterans", limit=20)
        self.assertEqual(
            [m.chunk.position for m in self.store.search("veteran", limit=20)],
            [m.chunk.position for m in plural],
        )
        self.assertEqual({m.document.pmcid for m in plural}, {"PMC12826829"})

    def test_filters_by_section_and_limits_results(self) -> None:
        matches = self.store.search("adhd", limit=20, sections=("methods", "title"))

        self.assertTrue(matches)
        self.assertLessEqual({m.chunk.section_name for m in matches}, {"methods", "title"})
        self.assertEqual(len(self.store.search("adhd", limit=2)), 2)

    def test_search_syntax_in_the_query_is_plain_text(self) -> None:
        plain = self.store.search("adhd", limit=20)
        for query in (
            'adhd*', '"adhd', 'adhd"', '^adhd', '-adhd',
            '(adhd)', 'adhd:', '{adhd}',
        ):
            with self.subTest(query=query):
                self.assertEqual(self.store.search(query, limit=20), plain)
        # Operator words are searched for as ordinary words.
        self.assertEqual(self.store.search("adhd OR veterans", limit=20), ())
        for match in self.store.search("NEAR(adhd", limit=20):
            self.assertIn("near", match.chunk.text.casefold())

    def test_rejects_empty_queries_and_bad_limits(self) -> None:
        for query in ("", "   ", "*:()"):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    self.store.search(query, limit=5)
        with self.assertRaises(ValueError):
            self.store.search("adhd", limit=0)

    def test_replaced_and_rejected_text_leaves_the_index(self) -> None:
        one_section = (ArticleSection("abstract", None, ("Replacement wording.",)),)
        self.store.save(
            replace(DOCUMENT, pmcid_version="PMC10009402.2"),
            one_section, chunk_sections(one_section),
        )
        self.assertEqual(self.store.search("adhd", limit=20), ())
        self.assertEqual(len(self.store.search("replacement", limit=20)), 1)

        self.store.reject(
            "PMC10009402", "retracted", recorded_at="2026-11-18T00:00:00Z"
        )

        self.assertEqual(self.store.search("replacement", limit=20), ())
        self.assertTrue(self.store.search("veterans", limit=20))


if __name__ == "__main__":
    unittest.main()
