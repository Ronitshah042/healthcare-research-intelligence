"""A small request-to-result pipeline for the PubMed ingestion client."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from .metadata import PaperMetadata
from .pubmed import (
    PUBMED_ATTRIBUTION,
    PubMedAttribution,
    PubMedClient,
    PubMedSearchPage,
    SearchMode,
)


class SearchRequestValidationError(ValueError):
    """Raised when a pipeline search request cannot be routed safely."""


@dataclass(frozen=True)
class SearchRequest:
    """One source-neutral search request accepted by the ingestion pipeline."""

    query: str
    search_mode: SearchMode
    page: int = 1
    page_size: int = 20
    publication_year: int | None = None
    publication_year_end: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.search_mode, SearchMode):
            raise SearchRequestValidationError(
                "search_mode must be a supported SearchMode value."
            )


@dataclass(frozen=True)
class StandardisedPaper:
    """One consistently shaped citation, with absent source fields set to ``None``."""

    pmid: str | None
    doi: str | None
    title: str | None
    authors: tuple[str, ...]
    journal: str | None
    publication_year: int | None
    abstract_available: bool
    source_identifier: str | None
    source_url: str | None


@dataclass(frozen=True)
class SearchResult:
    """A consistent metadata-only result returned by the ingestion pipeline."""

    records: tuple[StandardisedPaper, ...]
    # PubMed's full match count, before current-page validation or deduplication.
    total_count: int
    # Unique, valid records returned by this pipeline result's current page.
    normalised_count: int
    page: int
    page_size: int
    search_mode: SearchMode
    fallback_used: bool
    invalid_record_count: int
    duplicate_record_count: int
    source_name: str = "PubMed"
    attribution: PubMedAttribution = PUBMED_ATTRIBUTION


class SearchPipeline:
    """Route one search request through a PubMed client and normalise its result."""

    def __init__(self, pubmed_client: PubMedClient) -> None:
        self._pubmed_client = pubmed_client

    def search(self, request: SearchRequest) -> SearchResult:
        """Execute one request and return only reusable, validated paper metadata."""
        page = self._search_pubmed(request)
        records, invalid_record_count, duplicate_record_count = _standardise_records(
            page.records
        )
        return SearchResult(
            records=records,
            total_count=page.total_count,
            normalised_count=len(records),
            page=page.page,
            page_size=page.page_size,
            search_mode=page.search_mode,
            fallback_used=page.fallback_used,
            invalid_record_count=page.invalid_record_count + invalid_record_count,
            duplicate_record_count=duplicate_record_count,
            attribution=page.attribution,
        )

    def _search_pubmed(self, request: SearchRequest) -> PubMedSearchPage:
        common_arguments = {
            "page": request.page,
            "page_size": request.page_size,
            "publication_year": request.publication_year,
            "publication_year_end": request.publication_year_end,
        }

        if request.search_mode is SearchMode.TITLE:
            return self._pubmed_client.search_title(request.query, **common_arguments)
        if request.search_mode is SearchMode.TITLE_ABSTRACT:
            return self._pubmed_client.search_title_abstract(
                request.query, **common_arguments
            )
        if request.search_mode is SearchMode.AUTHOR:
            return self._pubmed_client.search_author(request.query, **common_arguments)
        if request.search_mode is SearchMode.DOI:
            return self._pubmed_client.lookup_doi(request.query, **common_arguments)
        return self._pubmed_client.lookup_pmid(request.query, **common_arguments)


def _standardise_records(
    records: tuple[object, ...],
) -> tuple[tuple[StandardisedPaper, ...], int, int]:
    standardised_records: list[StandardisedPaper] = []
    invalid_record_count = 0

    for record in records:
        standardised = _standardise_record(record)
        if standardised is None:
            invalid_record_count += 1
        else:
            standardised_records.append(standardised)

    deduplicated_records = _deduplicate_records(standardised_records)
    duplicate_record_count = len(standardised_records) - len(deduplicated_records)
    return tuple(deduplicated_records), invalid_record_count, duplicate_record_count


def _standardise_record(record: object) -> StandardisedPaper | None:
    metadata = getattr(record, "metadata", None)
    if not isinstance(metadata, PaperMetadata):
        return None

    title = _normalise_optional_text(metadata.title)
    if title is None:
        return None

    authors = tuple(
        author
        for author in (_normalise_optional_text(author) for author in metadata.authors)
        if author is not None
    )
    publication_year = metadata.publication_year
    if isinstance(publication_year, bool) or not isinstance(publication_year, int):
        publication_year = None

    return StandardisedPaper(
        pmid=_normalise_pmid(getattr(record, "pmid", None)),
        doi=_normalise_doi(getattr(record, "doi", None)),
        title=title,
        authors=authors,
        journal=_normalise_optional_text(getattr(record, "journal_title", None)),
        publication_year=publication_year,
        abstract_available=bool(getattr(record, "abstract_available", False)),
        source_identifier=_normalise_optional_text(metadata.source_identifier),
        source_url=_normalise_optional_text(metadata.source_url),
    )


def _deduplicate_records(
    records: list[StandardisedPaper],
) -> list[StandardisedPaper]:
    parent = list(range(len(records)))
    identifiers: dict[str, int] = {}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    for index, record in enumerate(records):
        for identifier in _identifiers_for(record):
            previous_index = identifiers.get(identifier)
            if previous_index is None:
                identifiers[identifier] = index
            else:
                union(previous_index, index)

    groups: dict[int, list[StandardisedPaper]] = {}
    for index, record in enumerate(records):
        groups.setdefault(find(index), []).append(record)
    return [_merge_records(group) for group in groups.values()]


def _identifiers_for(record: StandardisedPaper) -> tuple[str, ...]:
    identifiers: list[str] = []
    if record.pmid is not None:
        identifiers.append(f"pmid:{record.pmid}")
    if record.doi is not None:
        identifiers.append(f"doi:{record.doi}")
    return tuple(identifiers)


def _merge_records(records: list[StandardisedPaper]) -> StandardisedPaper:
    return StandardisedPaper(
        pmid=_first_present(record.pmid for record in records),
        doi=_first_present(record.doi for record in records),
        title=_first_present(record.title for record in records),
        authors=next((record.authors for record in records if record.authors), ()),
        journal=_first_present(record.journal for record in records),
        publication_year=next(
            (record.publication_year for record in records if record.publication_year),
            None,
        ),
        abstract_available=any(record.abstract_available for record in records),
        source_identifier=_first_present(
            record.source_identifier for record in records
        ),
        source_url=_first_present(record.source_url for record in records),
    )


def _first_present(values: Iterable[str | None]) -> str | None:
    for value in values:
        if value is not None:
            return value
    return None


def _normalise_pmid(value: object) -> str | None:
    pmid = _normalise_optional_text(value)
    if pmid is None or re.fullmatch(r"[0-9]+", pmid, re.ASCII) is None:
        return None
    return pmid


def _normalise_doi(value: object) -> str | None:
    doi = _normalise_optional_text(value)
    if doi is None or re.fullmatch(r"10\.[0-9]{4,9}/\S+", doi, re.ASCII) is None:
        return None
    return doi.lower()


def _normalise_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalised = " ".join(value.split())
    return normalised or None
