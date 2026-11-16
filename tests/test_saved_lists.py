"""Tests for saved lists of stored papers, using temporary databases."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ingestion.pipeline import StandardisedPaper
from ingestion.saved_lists import (
    PaperNotFoundError,
    SavedList,
    SavedListExistsError,
    SavedListNotFoundError,
    SavedListStore,
)
from ingestion.storage import PaperStore


class SavedListStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "papers.sqlite3"
        self.papers = PaperStore(path)
        self.lists = SavedListStore(path)
        paper = StandardisedPaper(
            pmid="1", doi="10.1234/one", title="First", authors=("Ada Lovelace",),
            journal="Journal", publication_year=2024, abstract_available=True,
            source_identifier="PMID:1", source_url=None, source_name="PubMed",
        )
        self.papers.save(paper)
        self.papers.save(replace(paper, pmid="2", doi=None, title="Second"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_creates_lists_and_reports_them_in_creation_order(self) -> None:
        self.assertEqual(self.lists.lists(), ())

        created = self.lists.create("  To   read ")
        self.lists.create("Methods")

        self.assertEqual(created, SavedList(name="To read", paper_count=0))
        self.assertEqual(
            [saved.name for saved in self.lists.lists()], ["To read", "Methods"]
        )

    def test_rejects_duplicate_names_ignoring_case(self) -> None:
        self.lists.create("Reading")
        with self.assertRaises(SavedListExistsError):
            self.lists.create("reading")

    def test_rejects_invalid_names(self) -> None:
        for name in ("", "   ", "x" * 101, None, 5):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.lists.create(name)  # type: ignore[arg-type]

    def test_adds_papers_by_pmid_or_doi_once_and_keeps_order(self) -> None:
        self.lists.create("Reading")

        self.assertTrue(self.lists.add("Reading", pmid="2"))
        self.assertTrue(self.lists.add("reading", doi="https://doi.org/10.1234/ONE"))
        self.assertFalse(self.lists.add("Reading", pmid="1"))

        self.assertEqual(
            [paper.pmid for paper in self.lists.papers("Reading")], ["2", "1"]
        )
        self.assertEqual(self.lists.lists(), (SavedList("Reading", 2),))

    def test_listed_papers_keep_their_attribution(self) -> None:
        self.lists.create("Reading")
        self.lists.add("Reading", pmid="1")

        paper = self.lists.papers("Reading")[0]

        self.assertIsNotNone(paper.attribution)
        self.assertEqual(paper.pubmed_url, "https://pubmed.ncbi.nlm.nih.gov/1/")

    def test_removes_papers_and_reports_whether_anything_changed(self) -> None:
        self.lists.create("Reading")
        self.lists.add("Reading", pmid="1")
        self.lists.add("Reading", pmid="2")

        self.assertTrue(self.lists.remove("Reading", pmid="1"))
        self.assertFalse(self.lists.remove("Reading", pmid="1"))
        self.assertFalse(self.lists.remove("Reading", pmid="999"))

        self.assertEqual([p.pmid for p in self.lists.papers("Reading")], ["2"])
        self.assertIsNotNone(self.papers.get(pmid="1"))

    def test_a_paper_can_be_in_several_lists(self) -> None:
        self.lists.create("A")
        self.lists.create("B")
        self.lists.add("A", pmid="1")
        self.lists.add("B", pmid="1")
        self.lists.remove("A", pmid="1")

        self.assertEqual(self.lists.papers("A"), ())
        self.assertEqual([p.pmid for p in self.lists.papers("B")], ["1"])

    def test_renames_a_list_and_keeps_its_papers(self) -> None:
        self.lists.create("Reading")
        self.lists.create("Methods")
        self.lists.add("Reading", pmid="1")

        renamed = self.lists.rename("reading", "  To   read ")

        self.assertEqual(renamed, SavedList("To read", 1))
        self.assertEqual([p.pmid for p in self.lists.papers("To read")], ["1"])
        self.assertEqual(
            [saved.name for saved in self.lists.lists()], ["To read", "Methods"]
        )
        with self.assertRaises(SavedListNotFoundError):
            self.lists.papers("Reading")

    def test_rename_can_change_only_the_letter_case(self) -> None:
        self.lists.create("reading")

        self.assertEqual(self.lists.rename("reading", "Reading").name, "Reading")
        self.assertEqual([saved.name for saved in self.lists.lists()], ["Reading"])

    def test_rename_rejects_taken_missing_and_invalid_names(self) -> None:
        self.lists.create("Reading")
        self.lists.create("Methods")
        with self.assertRaises(SavedListExistsError):
            self.lists.rename("Reading", "methods")
        with self.assertRaises(SavedListNotFoundError):
            self.lists.rename("Missing", "Other")
        with self.assertRaises(ValueError):
            self.lists.rename("Reading", "   ")
        self.assertEqual(
            [saved.name for saved in self.lists.lists()], ["Reading", "Methods"]
        )

    def test_deletes_a_list_but_not_its_papers_or_other_lists(self) -> None:
        self.lists.create("Reading")
        self.lists.create("Methods")
        self.lists.add("Reading", pmid="1")
        self.lists.add("Reading", pmid="2")
        self.lists.add("Methods", pmid="1")

        deleted = self.lists.delete("READING")

        self.assertEqual(deleted, SavedList("Reading", 2))
        self.assertEqual(self.lists.lists(), (SavedList("Methods", 1),))
        self.assertIsNotNone(self.papers.get(pmid="2"))
        with self.assertRaises(SavedListNotFoundError):
            self.lists.delete("Reading")
        self.lists.create("Reading")
        self.assertEqual(self.lists.papers("Reading"), ())

    def test_unknown_list_or_paper_raises_specific_errors(self) -> None:
        self.lists.create("Reading")
        for action in (
            lambda: self.lists.papers("Missing"),
            lambda: self.lists.add("Missing", pmid="1"),
            lambda: self.lists.remove("Missing", pmid="1"),
        ):
            with self.assertRaises(SavedListNotFoundError):
                action()
        with self.assertRaises(PaperNotFoundError):
            self.lists.add("Reading", pmid="999")

    def test_rejects_invalid_or_ambiguous_identifiers(self) -> None:
        self.lists.create("Reading")
        for arguments in ({}, {"pmid": "1", "doi": "10.1234/one"}, {"pmid": "abc"}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.lists.add("Reading", **arguments)


if __name__ == "__main__":
    unittest.main()
