"""UI-agnostic operations over PubMed ingestion, local search, and saved lists.

Every function returns a typed result object and never prints. A command-line
interface, web API, or UI should call only this module. The full contract is
in ``docs/SERVICE_API.md``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path

from ingestion import (
    PUBMED_ATTRIBUTION,
    PaperIdentityConflictError,
    PaperStorageError,
    PaperStore,
    PaperStoreStats,
    PubMedAttribution,
    PubMedClient,
    PubMedClientConfig,
    PubMedConfigurationError,
    PubMedError,
    SearchMode,
    SearchPipeline,
    SearchRequest,
    StoredPaper,
)
from ingestion.saved_lists import (
    PaperNotFoundError,
    SavedList,
    SavedListExistsError,
    SavedListNotFoundError,
    SavedListStore,
)

from .config import resolve_database_path
from .errors import InvalidRequestError, as_invalid_request as _as_invalid_request
from .export import (
    EXPORT_FORMATS,
    papers_to_citations,
    papers_to_csv,
    papers_to_plain,
)


SEARCH_STRATEGIES = ("keyword", "fuzzy", "boolean")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DEFAULT_INGEST_RECORDS = 100
MAX_INGEST_RECORDS = 1000
_INGEST_PAGE_SIZE = 20
# Start of the range when only an end year is given; PubMed holds nothing older.
_EARLIEST_YEAR = 1500

from .fulltext_services import (  # re-exported so callers need one import
    DEFAULT_FULLTEXT_ARTICLES,
    MAX_FULLTEXT_ARTICLES,
    FullTextDocument,
    FullTextFetchResult,
    FullTextRecheckResult,
    FullTextRejection,
    FullTextStats,
    LicencePolicyError,
    fetch_fulltext,
    get_fulltext_stats,
    recheck_fulltext,
)
from .retrieval_services import (  # re-exported so callers need one import
    DEFAULT_PASSAGE_RESULTS,
    MAX_PASSAGE_RESULTS,
    SEARCH_METHODS,
    SECTION_NAMES,
    EmbedderStatus,
    EmbedderUnavailableError,
    EmbedPassagesResult,
    PassageMatch,
    PassageSearchResult,
    embed_passages,
    prepare_embedder,
    search_passages,
)

__all__ = [
    "SEARCH_STRATEGIES",
    "EXPORT_FORMATS",
    "SearchMode",
    "InvalidRequestError",
    "PaperNotFoundError",
    "SavedListExistsError",
    "SavedListNotFoundError",
    "PaperStorageError",
    "PubMedError",
    "PubMedConfigurationError",
    "PubMedAttribution",
    "StoredPaper",
    "SavedList",
    "PaperStoreStats",
    "IngestResult",
    "SearchResults",
    "ListChange",
    "ListContents",
    "ListExport",
    "ingest",
    "search_papers",
    "get_paper",
    "get_stats",
    "create_list",
    "rename_list",
    "delete_list",
    "add_to_list",
    "remove_from_list",
    "get_lists",
    "get_list",
    "export_list",
    "LicencePolicyError",
    "FullTextDocument",
    "FullTextRejection",
    "FullTextStats",
    "FullTextFetchResult",
    "FullTextRecheckResult",
    "fetch_fulltext",
    "recheck_fulltext",
    "get_fulltext_stats",
    "DEFAULT_PASSAGE_RESULTS",
    "MAX_PASSAGE_RESULTS",
    "SECTION_NAMES",
    "PassageMatch",
    "search_passages",
    "SEARCH_METHODS",
    "EmbedderStatus",
    "EmbedderUnavailableError",
    "EmbedPassagesResult",
    "PassageSearchResult",
    "embed_passages",
    "prepare_embedder",
]


@dataclass(frozen=True)
class IngestResult:
    """Counts from one PubMed ingest run."""

    query: str
    mode: SearchMode
    requested_max: int
    # PubMed's overall match count for the query.
    total_available: int
    # Valid records received from PubMed, at most ``requested_max``.
    fetched: int
    # Distinct papers inserted or updated in the database.
    saved: int
    # Repeats of a paper already handled in this run.
    duplicates: int
    # Records PubMed returned that could not be used, including books.
    invalid_skipped: int
    # Records not saved because their PMID and DOI contradict stored papers.
    conflicts: int
    fallback_used: bool
    attribution: PubMedAttribution = PUBMED_ATTRIBUTION


@dataclass(frozen=True)
class SearchResults:
    """One page of local search results and the overall match count."""

    query: str
    strategy: str
    papers: tuple[StoredPaper, ...]
    total_count: int
    page: int
    page_size: int
    total_pages: int
    # Set when any paper on the page is PubMed-derived; must then be displayed.
    attribution: PubMedAttribution | None


@dataclass(frozen=True)
class ListChange:
    """The outcome of adding a paper to, or removing it from, a saved list."""

    saved_list: SavedList
    paper: StoredPaper | None
    # ``False`` when the list already had (or did not have) the paper.
    changed: bool


@dataclass(frozen=True)
class ListContents:
    """A saved list and its papers in the order they were added."""

    saved_list: SavedList
    papers: tuple[StoredPaper, ...]
    attribution: PubMedAttribution | None


@dataclass(frozen=True)
class ListExport:
    """A saved list rendered as text in one export format."""

    list_name: str
    export_format: str
    content: str
    paper_count: int
    attribution: PubMedAttribution | None


def ingest(
    query: str,
    *,
    mode: SearchMode | str = SearchMode.TITLE_ABSTRACT,
    max_records: int = DEFAULT_INGEST_RECORDS,
    year_from: int | None = None,
    year_to: int | None = None,
    database_path: str | Path | None = None,
    client: PubMedClient | None = None,
) -> IngestResult:
    """Fetch up to ``max_records`` PubMed citations and save them locally."""
    if not isinstance(query, str) or not query.strip():
        raise InvalidRequestError("query must be non-empty text.")
    search_mode = _search_mode(mode)
    if (
        isinstance(max_records, bool)
        or not isinstance(max_records, int)
        or not 1 <= max_records <= MAX_INGEST_RECORDS
    ):
        raise InvalidRequestError(
            f"max_records must be between 1 and {MAX_INGEST_RECORDS}."
        )
    publication_year, publication_year_end = _year_range(year_from, year_to)

    # Build the client first so missing PubMed settings fail before a
    # database file is created.
    pipeline = SearchPipeline(client if client is not None else build_pubmed_client())
    store = PaperStore(resolve_database_path(database_path))
    total_available = fetched = saved = duplicates = invalid = conflicts = 0
    fallback_used = False
    handled: set[str] = set()
    page = 1
    while fetched < max_records:
        with _as_invalid_request():
            result = pipeline.search(
                SearchRequest(
                    query=query,
                    search_mode=search_mode,
                    page=page,
                    page_size=_INGEST_PAGE_SIZE,
                    publication_year=publication_year,
                    publication_year_end=publication_year_end,
                )
            )
        total_available = result.total_count
        fallback_used = fallback_used or result.fallback_used
        invalid += result.invalid_record_count
        duplicates += result.duplicate_record_count
        for record in result.records[: max_records - fetched]:
            fetched += 1
            try:
                stored = store.save(record)
            except PaperIdentityConflictError:
                conflicts += 1
                continue
            key = stored.pmid or stored.doi or ""
            if key in handled:
                duplicates += 1
            else:
                handled.add(key)
                saved += 1
        # Read at most ``max_records`` PubMed positions, so the number of
        # requests is known in advance even when many records are unusable.
        if page * _INGEST_PAGE_SIZE >= min(total_available, max_records):
            break
        page += 1

    return IngestResult(
        query=query,
        mode=search_mode,
        requested_max=max_records,
        total_available=total_available,
        fetched=fetched,
        saved=saved,
        duplicates=duplicates,
        invalid_skipped=invalid,
        conflicts=conflicts,
        fallback_used=fallback_used,
    )


def search_papers(
    query: str,
    *,
    strategy: str = "keyword",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    year_from: int | None = None,
    year_to: int | None = None,
    abstract_available: bool | None = None,
    database_path: str | Path | None = None,
) -> SearchResults:
    """Search stored papers and return one page plus the total match count."""
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise InvalidRequestError("page must be a positive whole number.")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= MAX_PAGE_SIZE
    ):
        raise InvalidRequestError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")
    store = PaperStore(resolve_database_path(database_path))
    with _as_invalid_request():
        papers, total_count = store.search_with_total(
            query,
            limit=page_size,
            offset=(page - 1) * page_size,
            year_from=year_from,
            year_to=year_to,
            abstract_available=abstract_available,
            strategy=strategy,
        )
    return SearchResults(
        query=query,
        strategy=strategy,
        papers=papers,
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total_count / page_size),
        attribution=_attribution_for(papers),
    )


def get_paper(
    identifier: str, *, database_path: str | Path | None = None
) -> StoredPaper:
    """Return one stored paper by PMID or DOI, or raise ``PaperNotFoundError``."""
    lookup = _identifier_lookup(identifier)
    store = PaperStore(resolve_database_path(database_path))
    with _as_invalid_request():
        paper = store.get(**lookup)
    if paper is None:
        raise PaperNotFoundError("No stored paper has that identifier.")
    return paper


def get_stats(*, database_path: str | Path | None = None) -> PaperStoreStats:
    """Return counts of stored papers overall, by year, DOI, and abstract flag."""
    return PaperStore(resolve_database_path(database_path)).stats()


def create_list(name: str, *, database_path: str | Path | None = None) -> SavedList:
    """Create an empty saved list."""
    with _as_invalid_request():
        return _lists(database_path).create(name)


def rename_list(
    name: str, new_name: str, *, database_path: str | Path | None = None
) -> SavedList:
    """Rename a saved list, keeping its papers."""
    with _as_invalid_request():
        return _lists(database_path).rename(name, new_name)


def delete_list(name: str, *, database_path: str | Path | None = None) -> SavedList:
    """Delete a saved list and return it as it was; its papers stay stored."""
    with _as_invalid_request():
        return _lists(database_path).delete(name)


def add_to_list(
    name: str, identifier: str, *, database_path: str | Path | None = None
) -> ListChange:
    """Add a stored paper, given by PMID or DOI, to a saved list."""
    lookup = _identifier_lookup(identifier)
    lists = _lists(database_path)
    with _as_invalid_request():
        changed = lists.add(name, **lookup)
    paper = PaperStore(resolve_database_path(database_path)).get(**lookup)
    return ListChange(saved_list=_saved_list(lists, name), paper=paper, changed=changed)


def remove_from_list(
    name: str, identifier: str, *, database_path: str | Path | None = None
) -> ListChange:
    """Remove a paper, given by PMID or DOI, from a saved list."""
    lookup = _identifier_lookup(identifier)
    lists = _lists(database_path)
    with _as_invalid_request():
        changed = lists.remove(name, **lookup)
        paper = PaperStore(resolve_database_path(database_path)).get(**lookup)
    return ListChange(saved_list=_saved_list(lists, name), paper=paper, changed=changed)


def get_lists(*, database_path: str | Path | None = None) -> tuple[SavedList, ...]:
    """Return every saved list with its paper count, in creation order."""
    return _lists(database_path).lists()


def get_list(name: str, *, database_path: str | Path | None = None) -> ListContents:
    """Return one saved list and its papers."""
    lists = _lists(database_path)
    with _as_invalid_request():
        papers = lists.papers(name)
    return ListContents(
        saved_list=_saved_list(lists, name),
        papers=papers,
        attribution=_attribution_for(papers),
    )


def export_list(
    name: str,
    export_format: str = "csv",
    *,
    database_path: str | Path | None = None,
) -> ListExport:
    """Render a saved list as ``csv``, ``citations`` (Vancouver), or ``plain``."""
    if export_format not in EXPORT_FORMATS:
        raise InvalidRequestError(
            "export_format must be one of: " + ", ".join(EXPORT_FORMATS) + "."
        )
    contents = get_list(name, database_path=database_path)
    render = {
        "csv": papers_to_csv,
        "citations": papers_to_citations,
        "plain": papers_to_plain,
    }[export_format]
    return ListExport(
        list_name=contents.saved_list.name,
        export_format=export_format,
        content=render(contents.papers),
        paper_count=len(contents.papers),
        attribution=contents.attribution,
    )


def build_pubmed_client() -> PubMedClient:
    """Build the live PubMed client from ``PUBMED_*`` environment variables."""
    return PubMedClient(PubMedClientConfig.from_environment())


def _lists(database_path: str | Path | None) -> SavedListStore:
    return SavedListStore(resolve_database_path(database_path))


def _saved_list(lists: SavedListStore, name: str) -> SavedList:
    wanted = " ".join(name.split()).casefold()
    return next(
        saved for saved in lists.lists() if saved.name.casefold() == wanted
    )


def _attribution_for(papers: Iterable[StoredPaper]) -> PubMedAttribution | None:
    if any(paper.attribution is not None for paper in papers):
        return PUBMED_ATTRIBUTION
    return None


def _identifier_lookup(identifier: object) -> dict[str, str]:
    if not isinstance(identifier, str) or not identifier.strip():
        raise InvalidRequestError("identifier must be a PMID or DOI.")
    value = identifier.strip()
    if value.isascii() and value.isdigit():
        return {"pmid": value}
    return {"doi": value}


def _search_mode(mode: object) -> SearchMode:
    try:
        return SearchMode(mode)
    except ValueError as error:
        raise InvalidRequestError(
            "mode must be one of: " + ", ".join(m.value for m in SearchMode) + "."
        ) from error


def _year_range(
    year_from: int | None, year_to: int | None
) -> tuple[int | None, int | None]:
    """Map open-ended bounds onto the client's start year and end year."""
    for label, year in (("year_from", year_from), ("year_to", year_to)):
        if year is not None and (isinstance(year, bool) or not isinstance(year, int)):
            raise InvalidRequestError(f"{label} must be a whole number.")
    if year_from is None and year_to is None:
        return None, None
    if year_from is None:
        return _EARLIEST_YEAR, year_to
    if year_to is None:
        return year_from, max(year_from, date.today().year + 1)
    return year_from, year_to
