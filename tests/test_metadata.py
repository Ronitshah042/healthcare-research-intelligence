"""Tests for research-paper metadata normalisation."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import date

from ingestion.metadata import MetadataValidationError, normalise_paper_metadata


class NormalisePaperMetadataTests(unittest.TestCase):
    """Verify validation and normalisation of paper metadata."""

    @staticmethod
    def _valid_metadata() -> dict[str, object]:
        return {
            "title": "Example paper",
            "authors": ["Ada Lovelace"],
            "publication_year": 2025,
            "source_identifier": "PMID:12345",
        }

    def test_normalises_valid_metadata(self) -> None:
        metadata = normalise_paper_metadata(
            {
                "title": "  Effects   of exercise on sleep  ",
                "authors": [" Ada  Lovelace ", "Grace\tHopper"],
                "publication_year": "2025",
                "source_identifier": "  PMID:  12345  ",
                "abstract": "  A  short\nabstract.  ",
                "source_url": "https://example.org/papers/12345",
            }
        )

        self.assertEqual(metadata.title, "Effects of exercise on sleep")
        self.assertEqual(metadata.authors, ("Ada Lovelace", "Grace Hopper"))
        self.assertEqual(metadata.publication_year, 2025)
        self.assertEqual(metadata.source_identifier, "PMID: 12345")
        self.assertEqual(metadata.abstract, "A short abstract.")
        self.assertEqual(metadata.source_url, "https://example.org/papers/12345")

    def test_rejects_missing_required_fields(self) -> None:
        valid_metadata = self._valid_metadata()

        for field_name in valid_metadata:
            with self.subTest(field_name=field_name):
                incomplete_metadata = valid_metadata.copy()
                incomplete_metadata.pop(field_name)

                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(incomplete_metadata)

    def test_rejects_non_mapping_metadata(self) -> None:
        for metadata in (None, [], "metadata"):
            with self.subTest(metadata=metadata):
                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(metadata)

    def test_rejects_invalid_publication_years(self) -> None:
        valid_metadata = self._valid_metadata()
        valid_metadata.pop("publication_year")
        invalid_years = [0, -1, "not a year", date.today().year + 2]

        for invalid_year in invalid_years:
            with self.subTest(invalid_year=invalid_year):
                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(
                        {**valid_metadata, "publication_year": invalid_year}
                    )

    def test_rejects_empty_or_whitespace_required_values(self) -> None:
        invalid_values = ["", " \t\n "]

        for field_name in ("title", "source_identifier"):
            for invalid_value in invalid_values:
                with self.subTest(field_name=field_name, value=invalid_value):
                    metadata = self._valid_metadata()
                    metadata[field_name] = invalid_value

                    with self.assertRaises(MetadataValidationError):
                        normalise_paper_metadata(metadata)

        for invalid_authors in ([], [""], [" \t\n "]):
            with self.subTest(authors=invalid_authors):
                metadata = self._valid_metadata()
                metadata["authors"] = invalid_authors

                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(metadata)

    def test_rejects_invalid_author_inputs(self) -> None:
        invalid_authors = ["Ada Lovelace", None, ["Ada Lovelace", 1], [None]]

        for authors in invalid_authors:
            with self.subTest(authors=authors):
                metadata = self._valid_metadata()
                metadata["authors"] = authors

                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(metadata)

    def test_optional_fields_are_none_when_omitted_or_none(self) -> None:
        omitted = normalise_paper_metadata(self._valid_metadata())
        self.assertIsNone(omitted.abstract)
        self.assertIsNone(omitted.source_url)

        explicit_none = self._valid_metadata()
        explicit_none["abstract"] = None
        explicit_none["source_url"] = None
        metadata = normalise_paper_metadata(explicit_none)

        self.assertIsNone(metadata.abstract)
        self.assertIsNone(metadata.source_url)

    def test_blank_optional_fields_are_normalised_to_none(self) -> None:
        raw_metadata = self._valid_metadata()
        raw_metadata["abstract"] = " \n\t "
        raw_metadata["source_url"] = " \n\t "

        metadata = normalise_paper_metadata(raw_metadata)

        self.assertIsNone(metadata.abstract)
        self.assertIsNone(metadata.source_url)

    def test_rejects_invalid_optional_field_types(self) -> None:
        invalid_fields = {"abstract": 1, "source_url": ["https://example.org"]}

        for field_name, invalid_value in invalid_fields.items():
            with self.subTest(field_name=field_name):
                metadata = self._valid_metadata()
                metadata[field_name] = invalid_value

                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(metadata)

    def test_accepts_valid_http_and_https_source_urls(self) -> None:
        for source_url in (
            "http://example.org/papers/12345",
            "https://example.org/papers/12345",
        ):
            with self.subTest(source_url=source_url):
                raw_metadata = self._valid_metadata()
                raw_metadata["source_url"] = source_url

                metadata = normalise_paper_metadata(raw_metadata)

                self.assertEqual(metadata.source_url, source_url)

    def test_rejects_invalid_source_url_structures(self) -> None:
        invalid_urls = [
            "/papers/12345",
            "example.org/papers/12345",
            "ftp://example.org/papers/12345",
            "https://",
        ]

        for source_url in invalid_urls:
            with self.subTest(source_url=source_url):
                metadata = self._valid_metadata()
                metadata["source_url"] = source_url

                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(metadata)

    def test_rejects_malformed_source_urls_with_metadata_validation_error(self) -> None:
        malformed_urls = ["https://[::1", "https://example.org]"]

        for source_url in malformed_urls:
            with self.subTest(source_url=source_url):
                metadata = self._valid_metadata()
                metadata["source_url"] = source_url

                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(metadata)

    def test_normalised_metadata_is_immutable(self) -> None:
        metadata = normalise_paper_metadata(self._valid_metadata())

        with self.assertRaises(FrozenInstanceError):
            metadata.title = "Changed title"

    def test_accepts_publication_year_boundaries(self) -> None:
        valid_years = [1, date.today().year + 1]

        for publication_year in valid_years:
            with self.subTest(publication_year=publication_year):
                metadata = self._valid_metadata()
                metadata["publication_year"] = publication_year

                normalised_metadata = normalise_paper_metadata(metadata)

                self.assertEqual(
                    normalised_metadata.publication_year, publication_year
                )

    def test_rejects_invalid_publication_year_types(self) -> None:
        invalid_years = [True, False, 2025.0, "2025.0"]

        for publication_year in invalid_years:
            with self.subTest(publication_year=publication_year):
                metadata = self._valid_metadata()
                metadata["publication_year"] = publication_year

                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(metadata)

    def test_rejects_unicode_digit_year_with_metadata_validation_error(self) -> None:
        """Reject Unicode digits that cannot be converted to an integer year."""
        metadata = self._valid_metadata()
        metadata["publication_year"] = "²"

        with self.assertRaises(MetadataValidationError):
            normalise_paper_metadata(metadata)

    def test_rejects_non_ascii_digit_years_that_int_would_accept(self) -> None:
        for publication_year in ("٢٠٢٤", "２０２４"):
            with self.subTest(publication_year=publication_year):
                metadata = self._valid_metadata()
                metadata["publication_year"] = publication_year

                with self.assertRaises(MetadataValidationError):
                    normalise_paper_metadata(metadata)


if __name__ == "__main__":
    unittest.main()
