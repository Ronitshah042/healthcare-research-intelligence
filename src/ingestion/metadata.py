"""Normalisation and validation for basic research-paper metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import re
from urllib.parse import urlsplit


class MetadataValidationError(ValueError):
    """Raised when supplied research-paper metadata is invalid."""


@dataclass(frozen=True)
class PaperMetadata:
    """Validated metadata for one research paper."""

    title: str
    authors: tuple[str, ...]
    publication_year: int
    source_identifier: str
    abstract: str | None = None
    source_url: str | None = None


def normalise_paper_metadata(metadata: Mapping[str, object]) -> PaperMetadata:
    """Return validated paper metadata with consistently formatted text.

    Required fields are title, authors, publication_year, and source_identifier.
    The author sequence may be empty, because some citations list no authors.
    Publication years may range from 1 through the next calendar year.
    """
    if not isinstance(metadata, Mapping):
        raise MetadataValidationError("Metadata must be a mapping.")

    title = _normalise_required_text(metadata.get("title"), "title")
    authors = _normalise_authors(metadata.get("authors"))
    publication_year = _normalise_publication_year(metadata.get("publication_year"))
    source_identifier = _normalise_required_text(
        metadata.get("source_identifier"), "source_identifier"
    )
    abstract = _normalise_optional_text(metadata.get("abstract"), "abstract")
    source_url = _normalise_source_url(metadata.get("source_url"))

    return PaperMetadata(
        title=title,
        authors=authors,
        publication_year=publication_year,
        source_identifier=source_identifier,
        abstract=abstract,
        source_url=source_url,
    )


def _normalise_required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise MetadataValidationError(f"{field_name} is required and must be text.")

    normalised = _normalise_whitespace(value)
    if not normalised:
        raise MetadataValidationError(f"{field_name} must not be empty.")
    return normalised


def _normalise_optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MetadataValidationError(f"{field_name} must be text when provided.")

    normalised = _normalise_whitespace(value)
    return normalised or None


def _normalise_authors(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise MetadataValidationError("authors is required and must be a sequence of text.")

    return tuple(
        _normalise_required_text(author, "authors entry") for author in value
    )


def _normalise_publication_year(value: object) -> int:
    if isinstance(value, bool):
        raise MetadataValidationError("publication_year must be a whole number.")

    if isinstance(value, int):
        year = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        year = int(value.strip())
    else:
        raise MetadataValidationError("publication_year must be a whole number.")

    latest_valid_year = date.today().year + 1
    if not 1 <= year <= latest_valid_year:
        raise MetadataValidationError(
            f"publication_year must be between 1 and {latest_valid_year}."
        )
    return year


def _normalise_source_url(value: object) -> str | None:
    source_url = _normalise_optional_text(value, "source_url")
    if source_url is None:
        return None

    try:
        parsed_url = urlsplit(source_url)
    except ValueError as error:
        raise MetadataValidationError(
            "source_url must be an absolute HTTP or HTTPS URL when provided."
        ) from error

    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise MetadataValidationError(
            "source_url must be an absolute HTTP or HTTPS URL when provided."
        )
    return source_url


def _normalise_whitespace(value: str) -> str:
    return " ".join(value.split())
