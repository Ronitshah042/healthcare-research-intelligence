"""Tests for local SQLite paper storage."""

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ingestion.pipeline import StandardisedPaper
from ingestion.storage import (
    PaperIdentityConflictError,
    PaperStorageError,
    PaperStore,
)


class PaperStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = PaperStore(Path(self.temp_dir.name) / "papers.sqlite3")
        self.paper = StandardisedPaper(
            pmid="12345", doi="10.1234/example", title="Example study",
            authors=("Ada Lovelace", "Grace Hopper"), journal="Example Journal",
            publication_year=2024, abstract_available=True,
            source_identifier="PMID:12345", source_url="https://pubmed.ncbi.nlm.nih.gov/12345/",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_saves_and_reads_paper_by_pmid_and_doi(self) -> None:
        saved = self.store.save(self.paper)
        self.assertEqual(saved.title, self.paper.title)
        self.assertEqual(saved.authors, self.paper.authors)
        self.assertEqual(self.store.get(pmid="12345"), saved)
        self.assertEqual(self.store.get(doi="10.1234/example"), saved)

    def test_saves_again_as_update_instead_of_duplicate(self) -> None:
        self.store.save(self.paper)
        updated = replace(self.paper, title="Updated title")
        self.store.save(updated)
        self.assertEqual(len(self.store.list_papers()), 1)
        self.assertEqual(self.store.get(pmid="12345").title, "Updated title")

    def test_normalizes_doi_aliases_for_save_and_lookup(self) -> None:
        paper = replace(
            self.paper, pmid=None, doi="HTTPS://DOI.ORG/10.1234/Example"
        )
        self.store.save(paper)
        saved_again = replace(paper, doi="doi:10.1234/example", title="Updated")
        result = self.store.save(saved_again)
        self.assertEqual(len(self.store.list_papers()), 1)
        self.assertEqual(result.doi, "10.1234/example")
        self.assertEqual(self.store.get(doi="http://dx.doi.org/10.1234/EXAMPLE"), result)

    def test_finds_and_updates_legacy_unnormalised_doi_rows(self) -> None:
        self.store.save(replace(self.paper, pmid="999", doi="10.1234/other"))
        with sqlite3.connect(Path(self.temp_dir.name) / "papers.sqlite3") as connection:
            connection.execute(
                """INSERT INTO papers (doi, title, authors_json, abstract_available)
                VALUES (' HTTPS://doi.org/10.1234/Legacy ', 'Old', '[]', 0)"""
            )
        self.assertEqual(self.store.get(doi="10.1234/legacy").title, "Old")
        self.store.save(replace(self.paper, pmid=None, doi="10.1234/LEGACY", title="New"))
        self.assertEqual(len(self.store.list_papers()), 2)
        self.assertEqual(self.store.get(doi="10.1234/legacy").title, "New")
        self.assertEqual(self.store.get(doi="10.1234/other").pmid, "999")

    def test_enriches_record_with_identifier_arriving_later(self) -> None:
        self.store.save(replace(self.paper, doi=None))
        enriched = self.store.save(replace(self.paper, doi="10.1234/EXAMPLE"))
        self.assertEqual(len(self.store.list_papers()), 1)
        self.assertEqual(enriched.pmid, "12345")
        self.assertEqual(enriched.doi, "10.1234/example")

    def test_conflicting_identifiers_do_not_merge_or_change_records(self) -> None:
        self.store.save(replace(self.paper, doi=None))
        self.store.save(replace(self.paper, pmid=None, title="Other paper"))
        with self.assertRaises(PaperIdentityConflictError):
            self.store.save(self.paper)
        self.assertEqual(len(self.store.list_papers()), 2)

    def test_conflicting_new_doi_for_matched_pmid_is_rejected(self) -> None:
        self.store.save(self.paper)
        with self.assertRaises(PaperIdentityConflictError):
            self.store.save(replace(self.paper, doi="10.1234/different"))
        self.assertEqual(self.store.get(pmid="12345").doi, "10.1234/example")

    def test_search_matches_title_author_and_journal_with_pagination(self) -> None:
        self.store.save(self.paper)
        self.store.save(
            replace(self.paper, pmid="23456", doi=None, title="Another study")
        )
        self.assertEqual(self.store.search("EXAMPLE")[0].pmid, "12345")
        self.assertEqual(self.store.search("grace")[0].pmid, "12345")
        self.assertEqual(self.store.search("journal")[0].pmid, "12345")
        self.assertEqual(len(self.store.search("study", limit=1, offset=1)), 1)

    def test_search_requires_each_keyword_to_match(self) -> None:
        self.store.save(
            replace(self.paper, title="Sleep quality", journal="Research")
        )
        self.assertEqual(self.store.search("sleep quality")[0].pmid, "12345")
        self.assertEqual(self.store.search("sleep dementia"), ())

    def test_search_orders_results_by_relevance_then_insertion(self) -> None:
        title_match = replace(
            self.paper, pmid="100", doi=None, title="Cognition study",
            authors=("Researcher One",), journal="General Medicine",
        )
        author_match = replace(
            self.paper, pmid="200", doi=None, title="A study",
            authors=("Cognition Researcher",), journal="General Medicine",
        )
        journal_match = replace(
            self.paper, pmid="300", doi=None, title="A study",
            authors=("Researcher Three",), journal="Cognition Journal",
        )
        self.store.save(journal_match)
        self.store.save(author_match)
        self.store.save(title_match)
        self.assertEqual(
            [paper.pmid for paper in self.store.search("cognition")],
            ["100", "200", "300"],
        )

    def test_search_filters_inclusive_publication_year_range(self) -> None:
        self.store.save(replace(self.paper, publication_year=2020))
        self.store.save(
            replace(self.paper, pmid="23456", doi=None, publication_year=2023)
        )
        self.store.save(
            replace(self.paper, pmid="34567", doi=None, publication_year=2025)
        )
        self.assertEqual(
            [paper.publication_year for paper in self.store.search(
                "example", year_from=2020, year_to=2023
            )],
            [2020, 2023],
        )

    def test_search_filters_abstract_availability(self) -> None:
        self.store.save(replace(self.paper, abstract_available=True))
        self.store.save(
            replace(self.paper, pmid="23456", doi=None, abstract_available=False)
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "example", abstract_available=False
            )],
            ["23456"],
        )

    def test_search_paginates_after_relevance_ordering(self) -> None:
        self.store.save(
            replace(self.paper, pmid="300", doi=None, title="A study")
        )
        self.store.save(
            replace(self.paper, pmid="100", doi=None, title="Example study")
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search("example", limit=1)],
            ["100"],
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search("example", limit=1, offset=1)],
            ["300"],
        )

    def test_search_rejects_invalid_filters(self) -> None:
        with self.assertRaises(ValueError):
            self.store.search("study", year_from=2025, year_to=2020)
        with self.assertRaises(ValueError):
            self.store.search("study", abstract_available=1)

    def test_search_rejects_blank_query(self) -> None:
        with self.assertRaises(ValueError):
            self.store.search("  ")

    def test_fuzzy_strategy_finds_misspelled_title_keyword(self) -> None:
        self.store.save(replace(self.paper, title="Cardiac outcomes after surgery"))
        results = self.store.search("cardac outcomes", strategy="fuzzy")
        self.assertEqual([paper.pmid for paper in results], ["12345"])

    def test_fuzzy_strategy_finds_partial_title(self) -> None:
        self.store.save(
            replace(self.paper, title="Deep learning methods in clinical research")
        )
        results = self.store.search("deep learnig", strategy="fuzzy")
        self.assertEqual([paper.pmid for paper in results], ["12345"])

    def test_fuzzy_strategy_ranks_exact_matches_above_approximate(self) -> None:
        self.store.save(replace(self.paper, title="Cardiac outcomes"))
        self.store.save(
            replace(self.paper, pmid="23456", doi=None, title="Cardiac outcome")
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "cardiac outcomes", strategy="fuzzy"
            )],
            ["12345", "23456"],
        )

    def test_fuzzy_strategy_uses_stable_order_for_equal_scores(self) -> None:
        self.store.save(replace(self.paper, title="Cardiac outcomes"))
        self.store.save(
            replace(self.paper, pmid="23456", doi=None, title="Cardiac risks")
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "cardac", strategy="fuzzy"
            )],
            ["12345", "23456"],
        )

    def test_fuzzy_strategy_preserves_filters_and_paginates_ranked_results(self) -> None:
        self.store.save(
            replace(
                self.paper, pmid="200", doi=None, title="Cardiac outcomes",
                publication_year=2021, abstract_available=False,
            )
        )
        self.store.save(
            replace(
                self.paper, pmid="100", doi=None, title="Cardiac risks",
                publication_year=2023, abstract_available=False,
            )
        )
        self.store.save(
            replace(
                self.paper, pmid="300", doi=None, title="Cardiac report",
                publication_year=2018, abstract_available=False,
            )
        )
        query_options = {
            "strategy": "fuzzy",
            "year_from": 2020,
            "year_to": 2024,
            "abstract_available": False,
        }
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "cardac", limit=1, **query_options
            )],
            ["200"],
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "cardac", limit=1, offset=1, **query_options
            )],
            ["100"],
        )

    def test_fuzzy_strategy_rejects_unrelated_and_empty_results(self) -> None:
        self.store.save(replace(self.paper, title="Cardiac outcomes"))
        self.assertEqual(
            self.store.search("quantum cryptography", strategy="fuzzy"), ()
        )
        self.assertEqual(
            self.store.search("cardac", strategy="fuzzy", year_from=2030), ()
        )

    def test_fuzzy_keywords_cannot_reuse_one_title_token(self) -> None:
        self.store.save(replace(self.paper, title="Cardiac outcomes"))
        self.assertEqual(
            self.store.search("cardac cardiac", strategy="fuzzy"), ()
        )

    def test_keyword_strategy_remains_the_default_and_unchanged(self) -> None:
        self.store.save(replace(self.paper, title="Cardiac outcomes"))
        self.assertEqual(self.store.search("cardac"), ())
        self.assertEqual(
            self.store.search("cardiac outcomes"),
            self.store.search("cardiac outcomes", strategy="keyword"),
        )

    def test_fuzzy_strategy_rejects_unknown_strategy(self) -> None:
        with self.assertRaises(ValueError):
            self.store.search("study", strategy="approximate")

    def test_boolean_strategy_supports_and_or_and_not(self) -> None:
        self.store.save(replace(self.paper, title="Sleep and exercise"))
        self.store.save(
            replace(self.paper, pmid="23456", doi=None, title="Exercise only")
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "sleep AND exercise", strategy="boolean"
            )],
            ["12345"],
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "sleep OR exercise", strategy="boolean"
            )],
            ["12345", "23456"],
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "sleep AND NOT exercise", strategy="boolean"
            )],
            [],
        )

    def test_boolean_strategy_uses_not_and_or_precedence_and_grouping(self) -> None:
        self.store.save(replace(self.paper, title="Sleep only"))
        self.store.save(
            replace(self.paper, pmid="23456", doi=None, title="Sleep nutrition")
        )
        self.store.save(
            replace(self.paper, pmid="34567", doi=None, title="Exercise nutrition")
        )
        precedence = self.store.search(
            "sleep OR exercise AND nutrition", strategy="boolean"
        )
        grouped = self.store.search(
            "(sleep OR exercise) AND nutrition", strategy="boolean"
        )
        self.assertEqual([paper.pmid for paper in precedence], ["34567", "12345", "23456"])
        self.assertEqual([paper.pmid for paper in grouped], ["23456", "34567"])
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "NOT sleep AND nutrition", strategy="boolean"
            )],
            ["34567"],
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "NOT NOT sleep", strategy="boolean"
            )],
            ["12345", "23456"],
        )

    def test_boolean_strategy_matches_quoted_phrases(self) -> None:
        self.store.save(replace(self.paper, title="Machine learning in research"))
        self.store.save(
            replace(
                self.paper, pmid="23456", doi=None,
                title="Machine methods for learning",
            )
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                '"machine learning"', strategy="boolean"
            )],
            ["12345"],
        )

    def test_boolean_strategy_ranks_by_existing_field_relevance(self) -> None:
        self.store.save(
            replace(
                self.paper, pmid="100", doi=None, title="Cardiac study",
                authors=("Researcher",), journal="General Medicine",
            )
        )
        self.store.save(
            replace(
                self.paper, pmid="200", doi=None, title="General study",
                authors=("Cognition Researcher",), journal="General Medicine",
            )
        )
        self.store.save(
            replace(
                self.paper, pmid="300", doi=None, title="General study",
                authors=("Researcher",), journal="Cognition Journal",
            )
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "cardiac OR cognition", strategy="boolean"
            )],
            ["100", "200", "300"],
        )

    def test_boolean_strategy_breaks_relevance_ties_by_insertion_order(self) -> None:
        self.store.save(
            replace(self.paper, pmid="200", doi=None, title="Cognition study")
        )
        self.store.save(
            replace(self.paper, pmid="100", doi=None, title="Cognition review")
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "cognition", strategy="boolean"
            )],
            ["200", "100"],
        )

    def test_boolean_strategy_preserves_filters_and_pagination(self) -> None:
        self.store.save(
            replace(
                self.paper, pmid="200", doi=None, title="Sleep research",
                publication_year=2020, abstract_available=False,
            )
        )
        self.store.save(
            replace(
                self.paper, pmid="100", doi=None, title="Sleep research",
                publication_year=2023, abstract_available=False,
            )
        )
        self.store.save(
            replace(
                self.paper, pmid="300", doi=None, title="Sleep research",
                publication_year=2023, abstract_available=True,
            )
        )
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "sleep AND research", strategy="boolean", year_from=2020,
                year_to=2023, abstract_available=False, limit=1, offset=1,
            )],
            ["100"],
        )

    def test_boolean_strategy_rejects_malformed_queries(self) -> None:
        for query in (
            "AND sleep", "sleep AND", "(sleep OR exercise", "sleep exercise",
            '""', "sleep OR ()", '"unclosed phrase',
        ):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    self.store.search(query, strategy="boolean")

    def test_boolean_strategy_returns_empty_for_unsatisfied_expression(self) -> None:
        self.store.save(replace(self.paper, title="Sleep research"))
        self.assertEqual(
            self.store.search("sleep AND NOT sleep", strategy="boolean"), ()
        )

    def test_boolean_strategy_rejects_unknown_strategy_values(self) -> None:
        with self.assertRaises(ValueError):
            self.store.search("sleep", strategy="booleanx")

    def test_boolean_strategy_does_not_change_keyword_or_fuzzy_search(self) -> None:
        self.store.save(replace(self.paper, title="Cardiac outcomes"))
        self.assertEqual(self.store.search("cardac"), ())
        self.assertEqual(
            [paper.pmid for paper in self.store.search(
                "cardac", strategy="fuzzy"
            )],
            ["12345"],
        )
        self.assertEqual(
            self.store.search("cardiac outcomes"),
            self.store.search("cardiac outcomes", strategy="keyword"),
        )

    def test_search_matches_non_ascii_author_names(self) -> None:
        self.store.save(replace(self.paper, authors=("Anna Müller", "李 伟")))
        for strategy in ("keyword", "boolean"):
            with self.subTest(strategy=strategy):
                self.assertEqual(
                    [p.pmid for p in self.store.search("müller", strategy=strategy)],
                    ["12345"],
                )
                self.assertEqual(
                    [p.pmid for p in self.store.search("李", strategy=strategy)],
                    ["12345"],
                )
        self.assertEqual(self.store.search("u00fc"), ())

    def test_search_does_not_match_author_json_punctuation(self) -> None:
        self.store.save(self.paper)
        for query in ("[", ",", '"', "]"):
            with self.subTest(query=query):
                self.assertEqual(self.store.search(query), ())
        self.assertEqual(
            self.store.search('"Lovelace Grace"', strategy="boolean"), ()
        )

    def test_boolean_strategy_treats_hyphenated_operator_prefix_as_term(self) -> None:
        self.store.save(replace(self.paper, title="Not-for-profit hospitals"))
        self.store.save(replace(
            self.paper, pmid="2", doi="10.1234/other", title="Public hospitals"
        ))
        self.assertEqual(
            [p.pmid for p in self.store.search("not-for-profit", strategy="boolean")],
            ["12345"],
        )
        self.assertEqual(
            [p.pmid for p in self.store.search(
                "hospitals AND NOT(not-for-profit)", strategy="boolean"
            )],
            ["2"],
        )

    def test_boolean_strategy_negates_grouped_expressions(self) -> None:
        for pmid, title in (
            ("1", "Sleep and diet"), ("2", "Sleep only"),
            ("3", "Diet only"), ("4", "Neither"),
        ):
            self.store.save(replace(self.paper, pmid=pmid, doi=None, title=title))
        cases = {
            "NOT (sleep AND diet)": ["2", "3", "4"],
            "NOT (sleep OR diet)": ["4"],
            "NOT NOT sleep": ["1", "2"],
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(
                    [p.pmid for p in self.store.search(query, strategy="boolean")],
                    expected,
                )

    def test_conflicting_new_pmid_for_matched_doi_is_rejected(self) -> None:
        self.store.save(self.paper)
        with self.assertRaises(PaperIdentityConflictError):
            self.store.save(replace(self.paper, pmid="99999"))
        self.assertEqual(self.store.get(doi="10.1234/example").pmid, "12345")

    def test_rejects_non_standardised_paper_input(self) -> None:
        with self.assertRaises(TypeError):
            self.store.save({"pmid": "1"})  # type: ignore[arg-type]

    def test_reports_corrupt_stored_authors_as_storage_errors(self) -> None:
        with sqlite3.connect(Path(self.temp_dir.name) / "papers.sqlite3") as connection:
            connection.execute(
                """INSERT INTO papers (pmid, title, authors_json, abstract_available)
                VALUES ('7', 'Broken', 'not json', 0)"""
            )
        with self.assertRaises(PaperStorageError):
            self.store.get(pmid="7")
        with self.assertRaises(PaperStorageError):
            self.store.search("broken")

    def test_reports_unusable_database_path_as_storage_error(self) -> None:
        blocker = Path(self.temp_dir.name) / "not-a-directory"
        blocker.write_text("")
        with self.assertRaises(PaperStorageError):
            PaperStore(blocker / "papers.sqlite3")

    def test_saves_and_searches_paper_without_authors(self) -> None:
        saved = self.store.save(replace(self.paper, authors=()))
        self.assertEqual(saved.authors, ())
        self.assertEqual([p.pmid for p in self.store.search("example")], ["12345"])
        self.assertEqual(
            [p.pmid for p in self.store.search("example", strategy="fuzzy")],
            ["12345"],
        )

    def test_rejects_paper_without_persistent_identifier(self) -> None:
        paper = StandardisedPaper(
            pmid=None, doi=None, title="Example", authors=(), journal=None,
            publication_year=None, abstract_available=False,
            source_identifier=None, source_url=None,
        )
        with self.assertRaises(PaperStorageError):
            self.store.save(paper)

    def test_lookup_requires_exactly_one_identifier(self) -> None:
        with self.assertRaises(ValueError):
            self.store.get()
        with self.assertRaises(ValueError):
            self.store.get(pmid="12345", doi="10.1234/example")


if __name__ == "__main__":
    unittest.main()
