"""Tests for the shared DOI and PMID clean-up rules."""

import unittest

from ingestion.identifiers import strip_doi_prefix, strip_pmid_leading_zeros


class IdentifierTests(unittest.TestCase):
    def test_strips_one_doi_prefix_ignoring_case(self) -> None:
        cases = {
            "10.1234/Example": "10.1234/Example",
            "doi:10.1234/Example": "10.1234/Example",
            "DOI: 10.1234/Example": "10.1234/Example",
            "https://doi.org/10.1234/Example": "10.1234/Example",
            "HTTP://DX.DOI.ORG/10.1234/Example": "10.1234/Example",
            "doi:doi:10.1234/x": "doi:10.1234/x",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(strip_doi_prefix(value), expected)

    def test_strips_pmid_leading_zeros_but_keeps_one_digit(self) -> None:
        cases = {"123": "123", "0123": "123", "1020": "1020", "000": "0", "": ""}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(strip_pmid_leading_zeros(value), expected)


if __name__ == "__main__":
    unittest.main()
