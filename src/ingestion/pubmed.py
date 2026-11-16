"""PubMed citation search through the NCBI E-utilities API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import Enum
import hashlib
import logging
import math
import re
import socket
import ssl
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .identifiers import strip_doi_prefix, strip_pmid_leading_zeros
from .metadata import MetadataValidationError, PaperMetadata, normalise_paper_metadata
from .pubmed_config import PubMedClientConfig


LOGGER = logging.getLogger(__name__)
PUBMED_RECORD_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
# Short source label recorded with each citation; it decides attribution later.
PUBMED_SOURCE_NAME = "PubMed"
_PLAIN_TEXT_QUERY_FORBIDDEN_CHARACTERS = frozenset('[]()"*#')
_BOOLEAN_OPERATOR_PATTERN = re.compile(r"\b(?:AND|OR|NOT)\b")
_DOI_PATTERN = re.compile(r"10\.[0-9]{4,9}/[^\s\"]+", re.ASCII)
_PMID_PATTERN = re.compile(r"[0-9]+", re.ASCII)
_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class PubMedError(Exception):
    """Base exception for PubMed integration failures."""


class PubMedRequestValidationError(PubMedError, ValueError):
    """Raised when a local PubMed search request is invalid."""


class PubMedTimeoutError(PubMedError):
    """Raised when a PubMed API request times out."""


class PubMedTransportError(PubMedError):
    """Raised when the PubMed API cannot be reached."""


class PubMedHttpError(PubMedError):
    """Raised when PubMed returns an HTTP error response."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"PubMed returned HTTP status {status_code}.")


class PubMedResponseError(PubMedError):
    """Raised when PubMed returns malformed or unusable API data."""


class SearchMode(str, Enum):
    """Supported PubMed search modes."""

    TITLE = "title"
    TITLE_ABSTRACT = "title_abstract"
    AUTHOR = "author"
    DOI = "doi"
    PMID = "pmid"


@dataclass(frozen=True)
class PublicationYearFilter:
    """Inclusive primary publication-year search range."""

    start_year: int
    end_year: int | None = None

    def __post_init__(self) -> None:
        end_year = self.start_year if self.end_year is None else self.end_year
        _validate_year(self.start_year, "publication_year")
        _validate_year(end_year, "publication_year_end")
        if end_year < self.start_year:
            raise PubMedRequestValidationError(
                "publication_year_end must not be earlier than publication_year."
            )
        object.__setattr__(self, "end_year", end_year)

    @property
    def search_expression(self) -> str:
        """Return the documented PubMed publication-date filter expression."""
        if self.start_year == self.end_year:
            return f"{self.start_year}[pdat]"
        return f"{self.start_year}:{self.end_year}[pdat]"


@dataclass(frozen=True)
class PubMedAttribution:
    """Attribution text a presentation layer must display with results."""

    source_name: str
    source_url: str
    disclaimer_url: str
    independent_service_notice: str


PUBMED_ATTRIBUTION = PubMedAttribution(
    source_name="Source: PubMed, U.S. National Library of Medicine (NLM), NIH.",
    source_url="https://pubmed.ncbi.nlm.nih.gov/",
    disclaimer_url="https://www.ncbi.nlm.nih.gov/home/about/policies/",
    independent_service_notice=(
        "This independent service is not affiliated with, endorsed by, or "
        "sponsored by NCBI, NLM, NIH, or HHS."
    ),
)


@dataclass(frozen=True)
class CitationDetails:
    """Bibliographic fields that formal citation styles need.

    This is citation metadata only; it never holds abstract or full text.
    """

    # One entry per author as NLM writes them, such as ``Polack FP``;
    # collective (group) names are kept unchanged.
    authors: tuple[str, ...] = ()
    # NLM title abbreviation without full stops, such as ``N Engl J Med``.
    journal_abbreviation: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None


@dataclass(frozen=True)
class PubMedSearchRecord:
    """PubMed-specific citation information around validated paper metadata."""

    metadata: PaperMetadata
    pmid: str
    doi: str | None
    journal_title: str | None
    citation_date: str | None
    abstract_available: bool = False
    citation: CitationDetails = CitationDetails()


@dataclass(frozen=True)
class PubMedSearchPage:
    """One page of PubMed records and its search metadata."""

    records: tuple[PubMedSearchRecord, ...]
    total_count: int
    page: int
    page_size: int
    search_mode: SearchMode
    fallback_used: bool
    invalid_record_count: int
    attribution: PubMedAttribution = PUBMED_ATTRIBUTION


@dataclass(frozen=True)
class _SearchHistory:
    total_count: int
    webenv: str
    query_key: str


HttpTransport = Callable[[str, float], bytes]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class _RateLimitScheduler:
    """Reserve rate-limited request slots for one process authentication scope."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.next_request_at: float | None = None

    def reserve_slot(
        self,
        clock: Clock,
        sleeper: Sleeper,
        requests_per_second: float,
    ) -> None:
        with self.lock:
            now = clock()
            if self.next_request_at is not None and now < self.next_request_at:
                sleeper(self.next_request_at - now)
                now = clock()

            interval = 1 / requests_per_second
            scheduled_from = max(now, self.next_request_at or now)
            self.next_request_at = scheduled_from + interval


class PubMedClient:
    """Search PubMed citation metadata while observing configured request limits."""

    _process_rate_limiters: dict[str, _RateLimitScheduler] = {}
    _process_rate_limiters_lock = Lock()

    def __init__(
        self,
        config: PubMedClientConfig,
        *,
        transport: HttpTransport | None = None,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or _default_transport
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._rate_limiter = self._get_process_rate_limiter(config)
        self._rate_limit_lock = self._rate_limiter.lock

    @classmethod
    def _get_process_rate_limiter(
        cls, config: PubMedClientConfig
    ) -> _RateLimitScheduler:
        scope = _rate_limit_scope(config)
        with cls._process_rate_limiters_lock:
            limiter = cls._process_rate_limiters.get(scope)
            if limiter is None:
                limiter = _RateLimitScheduler()
                cls._process_rate_limiters[scope] = limiter
            return limiter

    @classmethod
    def _reset_process_rate_limiters_for_tests(cls) -> None:
        """Clear process-shared limiter state for isolated deterministic tests."""
        with cls._process_rate_limiters_lock:
            cls._process_rate_limiters.clear()

    def search_title(
        self,
        title: str,
        *,
        page: int = 1,
        page_size: int = 20,
        publication_year: int | None = None,
        publication_year_end: int | None = None,
    ) -> PubMedSearchPage:
        """Search titles, falling back to title/abstract only when no titles match."""
        return self._search(
            title,
            SearchMode.TITLE,
            page=page,
            page_size=page_size,
            publication_year=publication_year,
            publication_year_end=publication_year_end,
            allow_title_fallback=True,
        )

    def search_title_abstract(
        self,
        keywords: str,
        *,
        page: int = 1,
        page_size: int = 20,
        publication_year: int | None = None,
        publication_year_end: int | None = None,
    ) -> PubMedSearchPage:
        """Search PubMed title and abstract fields without fallback."""
        return self._search(
            keywords,
            SearchMode.TITLE_ABSTRACT,
            page=page,
            page_size=page_size,
            publication_year=publication_year,
            publication_year_end=publication_year_end,
            allow_title_fallback=False,
        )

    def search_author(
        self,
        author: str,
        *,
        page: int = 1,
        page_size: int = 20,
        publication_year: int | None = None,
        publication_year_end: int | None = None,
    ) -> PubMedSearchPage:
        """Search the PubMed author field without fallback."""
        return self._search(
            author,
            SearchMode.AUTHOR,
            page=page,
            page_size=page_size,
            publication_year=publication_year,
            publication_year_end=publication_year_end,
            allow_title_fallback=False,
        )

    def lookup_doi(
        self,
        doi: str,
        *,
        page: int = 1,
        page_size: int = 20,
        publication_year: int | None = None,
        publication_year_end: int | None = None,
    ) -> PubMedSearchPage:
        """Look up citations by PubMed article identifier without fallback."""
        return self._search(
            doi,
            SearchMode.DOI,
            page=page,
            page_size=page_size,
            publication_year=publication_year,
            publication_year_end=publication_year_end,
            allow_title_fallback=False,
        )

    def lookup_pmid(
        self,
        pmid: str,
        *,
        page: int = 1,
        page_size: int = 20,
        publication_year: int | None = None,
        publication_year_end: int | None = None,
    ) -> PubMedSearchPage:
        """Look up citations by PubMed ID without fallback."""
        return self._search(
            pmid,
            SearchMode.PMID,
            page=page,
            page_size=page_size,
            publication_year=publication_year,
            publication_year_end=publication_year_end,
            allow_title_fallback=False,
        )

    def _search(
        self,
        value: str,
        search_mode: SearchMode,
        *,
        page: int,
        page_size: int,
        publication_year: int | None,
        publication_year_end: int | None,
        allow_title_fallback: bool,
    ) -> PubMedSearchPage:
        _validate_pagination(page, page_size)
        year_filter = _create_year_filter(publication_year, publication_year_end)
        query_value = _normalise_search_input(value, search_mode)
        if not query_value:
            return _empty_page(search_mode, page, page_size)

        history = self._esearch(
            _build_search_term(query_value, search_mode, year_filter)
        )
        fallback_used = False

        if history.total_count == 0 and allow_title_fallback:
            fallback_used = True
            history = self._esearch(
                _build_search_term(query_value, SearchMode.TITLE_ABSTRACT, year_filter)
            )

        if history.total_count == 0 or (page - 1) * page_size >= history.total_count:
            return PubMedSearchPage(
                records=(),
                total_count=history.total_count,
                page=page,
                page_size=page_size,
                search_mode=search_mode,
                fallback_used=fallback_used,
                invalid_record_count=0,
            )

        records, invalid_record_count = self._efetch_page(
            history, retstart=(page - 1) * page_size, retmax=page_size
        )
        return PubMedSearchPage(
            records=records,
            total_count=history.total_count,
            page=page,
            page_size=page_size,
            search_mode=search_mode,
            fallback_used=fallback_used,
            invalid_record_count=invalid_record_count,
        )

    def _esearch(self, term: str) -> _SearchHistory:
        root = self._request_xml(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": term,
                "retmode": "xml",
                "retmax": "0",
                "usehistory": "y",
            },
        )
        _raise_for_api_error(root)

        count_text = _element_text(root.find("Count"))
        try:
            count = int(count_text or "")
        except ValueError as error:
            raise PubMedResponseError("PubMed ESearch response has no valid count.") from error
        if count < 0:
            raise PubMedResponseError("PubMed ESearch response has a negative count.")
        if count == 0:
            return _SearchHistory(total_count=0, webenv="", query_key="")

        webenv = _element_text(root.find("WebEnv"))
        query_key = _element_text(root.find("QueryKey"))
        if not webenv or not query_key:
            raise PubMedResponseError(
                "PubMed ESearch response is missing history-server information."
            )
        return _SearchHistory(total_count=count, webenv=webenv, query_key=query_key)

    def _efetch_page(
        self, history: _SearchHistory, *, retstart: int, retmax: int
    ) -> tuple[tuple[PubMedSearchRecord, ...], int]:
        root = self._request_xml(
            "efetch.fcgi",
            {
                "db": "pubmed",
                "query_key": history.query_key,
                "WebEnv": history.webenv,
                "retstart": str(retstart),
                "retmax": str(retmax),
                "retmode": "xml",
            },
        )
        _raise_for_api_error(root)

        articles = root.findall(".//PubmedArticle")
        # Books and book chapters use a different layout that is not mapped;
        # count them so a page never silently returns fewer records.
        book_count = len(root.findall(".//PubmedBookArticle"))
        if not articles and not book_count:
            raise PubMedResponseError(
                "PubMed EFetch response has no citation records for a non-empty search."
            )
        if book_count:
            LOGGER.warning("Skipping unsupported PubMed book records.")

        records: list[PubMedSearchRecord] = []
        invalid_record_count = book_count
        for article in articles:
            record = _map_pubmed_article(article)
            if record is None:
                invalid_record_count += 1
                LOGGER.warning("Skipping an invalid PubMed citation record.")
            else:
                records.append(record)
        return tuple(records), invalid_record_count

    def _request_xml(
        self, endpoint: str, parameters: dict[str, str]
    ) -> ElementTree.Element:
        response = self._request(endpoint, parameters)
        try:
            return ElementTree.fromstring(response)
        except ElementTree.ParseError as error:
            raise PubMedResponseError("PubMed returned malformed XML.") from error

    def _request(self, endpoint: str, parameters: dict[str, str]) -> bytes:
        request_parameters = {
            **parameters,
            "tool": self._config.tool,
            "email": self._config.email,
        }
        if self._config.api_key:
            request_parameters["api_key"] = self._config.api_key

        url = f"{self._config.base_url}{endpoint}?{urlencode(request_parameters)}"
        retries_used = 0

        while True:
            self._wait_for_request_slot()
            try:
                return self._transport(url, self._config.timeout_seconds)
            except HTTPError as error:
                if self._should_retry(retries_used, error.code):
                    self._wait_before_retry(retries_used, _retry_after_seconds(error))
                    retries_used += 1
                    continue
                raise PubMedHttpError(error.code) from error
            except (TimeoutError, socket.timeout) as error:
                if self._should_retry(retries_used):
                    self._wait_before_retry(retries_used)
                    retries_used += 1
                    continue
                raise PubMedTimeoutError("PubMed request timed out.") from error
            except URLError as error:
                if self._should_retry(retries_used) and not _is_certificate_error(
                    error.reason
                ):
                    self._wait_before_retry(retries_used)
                    retries_used += 1
                    continue
                if isinstance(error.reason, (TimeoutError, socket.timeout)):
                    raise PubMedTimeoutError("PubMed request timed out.") from error
                raise PubMedTransportError("Unable to reach the PubMed API.") from error
            except OSError as error:
                if self._should_retry(retries_used) and not _is_certificate_error(
                    error
                ):
                    self._wait_before_retry(retries_used)
                    retries_used += 1
                    continue
                raise PubMedTransportError("Unable to reach the PubMed API.") from error

    def _wait_for_request_slot(self) -> None:
        self._rate_limiter.reserve_slot(
            self._clock,
            self._sleeper,
            self._config.requests_per_second,
        )

    def _should_retry(self, retries_used: int, status_code: int | None = None) -> bool:
        if retries_used >= self._config.max_retries:
            return False
        return status_code is None or status_code in _RETRYABLE_HTTP_STATUS_CODES

    def _wait_before_retry(
        self, retries_used: int, retry_after_seconds: float | None = None
    ) -> None:
        backoff_seconds = self._config.retry_backoff_seconds * (2 ** retries_used)
        wait_seconds = max(backoff_seconds, retry_after_seconds or 0.0)
        LOGGER.warning("Retrying a transient PubMed request failure.")
        self._sleeper(wait_seconds)


def _default_transport(url: str, timeout_seconds: float) -> bytes:
    request = Request(url, headers={"User-Agent": "healthcare-research-intelligence"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _is_certificate_error(error: object) -> bool:
    # A failed certificate check will fail the same way on every retry.
    return isinstance(error, ssl.SSLCertVerificationError)


def _rate_limit_scope(config: PubMedClientConfig) -> str:
    if config.api_key is None:
        return "anonymous"
    api_key_digest = hashlib.sha256(config.api_key.encode("utf-8")).hexdigest()
    return f"api-key:{api_key_digest}"


def _retry_after_seconds(error: HTTPError) -> float | None:
    if error.headers is None:
        return None
    value = error.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _normalise_search_input(value: object, search_mode: SearchMode) -> str:
    if not isinstance(value, str):
        raise PubMedRequestValidationError("Search input must be text.")

    if search_mode in {
        SearchMode.TITLE,
        SearchMode.TITLE_ABSTRACT,
        SearchMode.AUTHOR,
    }:
        return _normalise_plain_text_query(value)
    if search_mode is SearchMode.DOI:
        return _normalise_doi(value)
    return _normalise_pmid(value)


def _normalise_plain_text_query(value: str) -> str:
    query = " ".join(value.split())
    if not query:
        return ""
    if any(character in _PLAIN_TEXT_QUERY_FORBIDDEN_CHARACTERS for character in query):
        raise PubMedRequestValidationError(
            "Plain-text searches must not contain PubMed query syntax characters."
        )
    if _BOOLEAN_OPERATOR_PATTERN.search(query):
        raise PubMedRequestValidationError(
            "Plain-text searches must not contain PubMed Boolean operators."
        )
    return query


def _normalise_doi(value: str) -> str:
    doi = strip_doi_prefix(value.strip())
    if not doi:
        return ""
    if not _DOI_PATTERN.fullmatch(doi):
        raise PubMedRequestValidationError(
            "doi must be a DOI beginning with 10.<registrant code>/ and contain no whitespace."
        )
    return doi


def _normalise_pmid(value: str) -> str:
    pmid = value.strip()
    if not pmid:
        return ""
    if not _PMID_PATTERN.fullmatch(pmid):
        raise PubMedRequestValidationError("pmid must contain ASCII digits only.")
    return strip_pmid_leading_zeros(pmid)


def _validate_pagination(page: object, page_size: object) -> None:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise PubMedRequestValidationError("page must be a positive whole number.")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= 20
    ):
        raise PubMedRequestValidationError("page_size must be between 1 and 20.")


def _create_year_filter(
    publication_year: int | None, publication_year_end: int | None
) -> PublicationYearFilter | None:
    if publication_year is None:
        if publication_year_end is not None:
            raise PubMedRequestValidationError(
                "publication_year is required when publication_year_end is set."
            )
        return None
    return PublicationYearFilter(publication_year, publication_year_end)


def _validate_year(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PubMedRequestValidationError(f"{field_name} must be a whole number.")
    if not 1 <= value <= date.today().year + 1:
        raise PubMedRequestValidationError(
            f"{field_name} must be between 1 and {date.today().year + 1}."
        )


def _build_search_term(
    query_value: str,
    search_mode: SearchMode,
    year_filter: PublicationYearFilter | None,
) -> str:
    if search_mode is SearchMode.DOI:
        term = f'"{query_value}"[aid]'
    else:
        field_tag = {
            SearchMode.TITLE: "ti",
            SearchMode.TITLE_ABSTRACT: "tiab",
            SearchMode.AUTHOR: "au",
            SearchMode.PMID: "pmid",
        }[search_mode]
        term = f"({query_value})[{field_tag}]"
    if year_filter is not None:
        term = f"{term} AND {year_filter.search_expression}"
    return term


def _empty_page(search_mode: SearchMode, page: int, page_size: int) -> PubMedSearchPage:
    return PubMedSearchPage(
        records=(),
        total_count=0,
        page=page,
        page_size=page_size,
        search_mode=search_mode,
        fallback_used=False,
        invalid_record_count=0,
    )


def _raise_for_api_error(root: ElementTree.Element) -> None:
    error = _element_text(root.find(".//ERROR"))
    if error:
        raise PubMedResponseError("PubMed reported an API error.")


def _map_pubmed_article(
    article: ElementTree.Element,
) -> PubMedSearchRecord | None:
    try:
        pmid = _element_text(article.find("./MedlineCitation/PMID"))
        if not pmid or not re.fullmatch(r"[0-9]+", pmid):
            return None

        title = _element_text(article.find("./MedlineCitation/Article/ArticleTitle"))
        authors = _extract_authors(article)
        publication_year, citation_date = _extract_primary_publication_year(article)
        if publication_year is None:
            return None

        metadata = normalise_paper_metadata(
            {
                "title": title,
                "authors": authors,
                "publication_year": publication_year,
                "source_identifier": f"PMID:{pmid}",
                "source_url": PUBMED_RECORD_URL.format(pmid=pmid),
                "abstract": None,
            }
        )
    except (AttributeError, TypeError, ValueError, MetadataValidationError):
        return None

    return PubMedSearchRecord(
        metadata=metadata,
        pmid=pmid,
        doi=_extract_doi(article),
        journal_title=_element_text(
            article.find("./MedlineCitation/Article/Journal/Title")
        ),
        citation_date=citation_date,
        abstract_available=(
            article.find("./MedlineCitation/Article/Abstract/AbstractText")
            is not None
        ),
        citation=_extract_citation_details(article),
    )


def _extract_citation_details(article: ElementTree.Element) -> CitationDetails:
    authors: list[str] = []
    for author in article.findall("./MedlineCitation/Article/AuthorList/Author"):
        collective_name = _element_text(author.find("CollectiveName"))
        last_name = _element_text(author.find("LastName"))
        initials = _element_text(author.find("Initials"))
        if collective_name:
            authors.append(collective_name)
        elif last_name:
            authors.append(f"{last_name} {initials}" if initials else last_name)

    abbreviation = _element_text(
        article.find("./MedlineCitation/MedlineJournalInfo/MedlineTA")
    )
    if abbreviation is None:
        iso_abbreviation = _element_text(
            article.find("./MedlineCitation/Article/Journal/ISOAbbreviation")
        )
        if iso_abbreviation:
            abbreviation = iso_abbreviation.replace(".", "")
    issue_path = "./MedlineCitation/Article/Journal/JournalIssue/"
    return CitationDetails(
        authors=tuple(authors),
        journal_abbreviation=abbreviation,
        volume=_element_text(article.find(issue_path + "Volume")),
        issue=_element_text(article.find(issue_path + "Issue")),
        pages=_element_text(
            article.find("./MedlineCitation/Article/Pagination/MedlinePgn")
        ),
    )


def _extract_authors(article: ElementTree.Element) -> list[str]:
    authors: list[str] = []
    for author in article.findall("./MedlineCitation/Article/AuthorList/Author"):
        collective_name = _element_text(author.find("CollectiveName"))
        if collective_name:
            authors.append(collective_name)
            continue

        last_name = _element_text(author.find("LastName"))
        fore_name = _element_text(author.find("ForeName"))
        initials = _element_text(author.find("Initials"))
        # PubMed supplies both; the initials only stand in for a missing forename.
        given_name = fore_name or initials
        if last_name:
            authors.append(f"{given_name} {last_name}" if given_name else last_name)
    return authors


def _extract_primary_publication_year(
    article: ElementTree.Element,
) -> tuple[str | None, str | None]:
    pub_date = article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate")
    if pub_date is None:
        return None, None

    year_element = pub_date.find("Year")
    if year_element is not None:
        year = _element_text(year_element)
        return year, year

    medline_date = _element_text(pub_date.find("MedlineDate"))
    if not medline_date:
        return None, None
    year_match = re.search(r"(?<![0-9])[0-9]{4}(?![0-9])", medline_date)
    if year_match is None:
        return None, medline_date
    return year_match.group(0), medline_date


def _extract_doi(article: ElementTree.Element) -> str | None:
    for identifier in article.findall("./PubmedData/ArticleIdList/ArticleId"):
        if identifier.attrib.get("IdType", "").lower() == "doi":
            return _element_text(identifier)
    return None


def _element_text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join("".join(element.itertext()).split())
    return value or None
