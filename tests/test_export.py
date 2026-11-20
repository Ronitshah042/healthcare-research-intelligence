"""Tests for CSV, Vancouver, and plain rendering of stored papers."""

import csv
import unittest
from dataclasses import replace
from io import StringIO

from hri.export import (
    CSV_COLUMNS,
    format_plain,
    format_vancouver,
    papers_to_citations,
    papers_to_csv,
    papers_to_plain,
)
from ingestion import PUBMED_ATTRIBUTION, CitationDetails, StoredPaper

PAPER = StoredPaper(
    pmid="33301246",
    doi="10.1056/nejmoa2034577",
    title="Safety and Efficacy of the BNT162b2 mRNA Covid-19 Vaccine.",
    authors=("Fernando P Polack", "Stephen J Thomas", "C4591001 Clinical Trial Group"),
    journal="The New England journal of medicine",
    publication_year=2020,
    abstract_available=True,
    source_identifier="PMID:33301246",
    source_url="https://pubmed.ncbi.nlm.nih.gov/33301246/",
    source_name="PubMed",
    citation=CitationDetails(
        authors=("Polack FP", "Thomas SJ", "C4591001 Clinical Trial Group"),
        journal_abbreviation="N Engl J Med",
        volume="383",
        issue="27",
        pages="2603-2615",
    ),
)


class VancouverTests(unittest.TestCase):
    def test_formats_a_complete_journal_article(self) -> None:
        self.assertEqual(
            format_vancouver(PAPER),
            "Polack FP, Thomas SJ, C4591001 Clinical Trial Group. Safety and "
            "Efficacy of the BNT162b2 mRNA Covid-19 Vaccine. N Engl J Med. "
            "2020;383(27):2603-2615. doi: 10.1056/nejmoa2034577. PMID: 33301246.",
        )

    def test_lists_six_authors_then_et_al(self) -> None:
        names = tuple(f"Author{number} A" for number in range(1, 8))
        six = replace(PAPER, citation=replace(PAPER.citation, authors=names[:6]))
        seven = replace(PAPER, citation=replace(PAPER.citation, authors=names))

        self.assertIn("Author6 A. Safety", format_vancouver(six))
        self.assertNotIn("et al", format_vancouver(six))
        self.assertIn("Author6 A, et al. Safety", format_vancouver(seven))
        self.assertNotIn("Author7", format_vancouver(seven))

    def test_leaves_out_parts_that_are_not_stored(self) -> None:
        cases = {
            "volume only": (
                replace(PAPER.citation, issue=None, pages=None), "2020;383."
            ),
            "no issue": (replace(PAPER.citation, issue=None), "2020;383:2603-2615."),
            "pages only": (
                replace(PAPER.citation, volume=None, issue=None), " 2020:2603-2615."
            ),
            "issue only": (
                replace(PAPER.citation, volume=None, pages=None), " 2020;(27)."
            ),
            "no locator": (
                replace(PAPER.citation, volume=None, issue=None, pages=None), " 2020. "
            ),
        }
        for name, (citation, expected) in cases.items():
            with self.subTest(case=name):
                self.assertIn(expected, format_vancouver(replace(PAPER, citation=citation)))

        bare = replace(
            PAPER, authors=(), journal=None, publication_year=None, doi=None,
            citation=None,
        )
        self.assertEqual(
            format_vancouver(bare),
            "Safety and Efficacy of the BNT162b2 mRNA Covid-19 Vaccine. PMID: 33301246.",
        )

    def test_falls_back_to_stored_names_without_citation_details(self) -> None:
        self.assertEqual(
            format_vancouver(replace(PAPER, citation=None)),
            "Fernando P Polack, Stephen J Thomas, C4591001 Clinical Trial Group. "
            "Safety and Efficacy of the BNT162b2 mRNA Covid-19 Vaccine. "
            "The New England journal of medicine. 2020. "
            "doi: 10.1056/nejmoa2034577. PMID: 33301246.",
        )

    def test_keeps_question_marks_and_avoids_double_full_stops(self) -> None:
        question = format_vancouver(replace(PAPER, title="Does it work?"))

        self.assertIn("Does it work? N Engl J Med.", question)
        self.assertNotIn("..", format_vancouver(PAPER))
        self.assertNotIn("?.", question)


class DocumentTests(unittest.TestCase):
    def test_citation_documents_are_numbered_and_end_with_attribution(self) -> None:
        second = replace(PAPER, pmid="2", doi=None, source_name=None)
        for render, formatter in (
            (papers_to_citations, format_vancouver), (papers_to_plain, format_plain),
        ):
            with self.subTest(render=render.__name__):
                lines = render([PAPER, second]).splitlines()

                self.assertEqual(lines[0], "1. " + formatter(PAPER))
                self.assertEqual(lines[1], "2. " + formatter(second))
                self.assertEqual(lines[2], "")
                self.assertEqual(lines[3], PUBMED_ATTRIBUTION.source_name)
                self.assertEqual(
                    lines[-1], PUBMED_ATTRIBUTION.independent_service_notice
                )

    def test_documents_without_pubmed_papers_have_no_attribution(self) -> None:
        other = replace(PAPER, source_name=None)

        self.assertEqual(papers_to_citations([other]).count("\n"), 1)
        self.assertEqual(papers_to_plain([]), "")

    def test_plain_layout_includes_the_pubmed_link(self) -> None:
        self.assertTrue(
            format_plain(PAPER).endswith(
                "2020. PMID: 33301246. doi:10.1056/nejmoa2034577. "
                "https://pubmed.ncbi.nlm.nih.gov/33301246/"
            )
        )

    def test_csv_has_citation_columns_and_formula_safe_cells(self) -> None:
        risky = replace(PAPER, pmid="2", title="=SUM(A1)", citation=None)

        rows = list(csv.DictReader(StringIO(papers_to_csv([PAPER, risky]))))

        self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
        self.assertEqual(
            (rows[0]["volume"], rows[0]["issue"], rows[0]["pages"]),
            ("383", "27", "2603-2615"),
        )
        self.assertEqual(rows[0]["authors"].split("; ")[0], "Fernando P Polack")
        self.assertEqual(rows[1]["title"], "'=SUM(A1)")
        self.assertEqual((rows[1]["volume"], rows[1]["pages"]), ("", ""))


if __name__ == "__main__":
    unittest.main()
