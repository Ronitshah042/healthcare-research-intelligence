"""Client for PubMed Central Open Access articles (docs/CONTENT_POLICY.md).

Only three HTTPS hosts are ever contacted, and every URL is built here:

- ``eutils.ncbi.nlm.nih.gov``: search PMC for open-access articles, and read
  PubMed's correction and retraction links for stored articles.
- ``pmc.ncbi.nlm.nih.gov``: the ID converter, for PMID/PMCID/DOI links and
  the current article version.
- ``pmc-oa-opendata.s3.amazonaws.com``: the PMC Cloud Service, for licence
  metadata and article XML.

``tool``, ``email``, and the API key go to NCBI hosts only. Requests share
the PubMed client's rate limiting, retries, and error redaction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json
import re
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

from .http import Clock, FetchErrors, HttpFetcher, HttpTransport, Sleeper
from .identifiers import strip_doi_prefix, strip_pmid_leading_zeros
from .pubmed import (
    _normalise_plain_text_query,
    _raise_for_api_error,
    PubMedError,
    ncbi_rate_limit_scope,
)
from .pubmed_config import PubMedClientConfig


EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
ID_CONVERTER_URL = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
CLOUD_URL = "https://pmc-oa-opendata.s3.amazonaws.com/"
ALLOWED_HOSTS = frozenset({
    "eutils.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "pmc-oa-opendata.s3.amazonaws.com",
})
_NCBI_HOSTS = frozenset({"eutils.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov"})
# Amazon publishes no rate for this bucket; this keeps requests polite.
CLOUD_REQUESTS_PER_SECOND = 3.0
ID_CONVERTER_BATCH_SIZE = 200
MAX_SEARCH_RESULTS = 200
ID_TYPES = ("pmcid", "pmid", "doi")
# PubMed ``CommentsCorrections`` link types and the notice flag each one sets.
# ``retracted`` is not stored as a flag: the article is deleted instead.
PUBMED_NOTICE_TYPES = {
    "ErratumIn": "corrected",
    "CorrectedandRepublishedIn": "corrected",
    "ExpressionOfConcernIn": "expression_of_concern",
    "PartialRetractionIn": "partially_retracted",
    "RetractionIn": "retracted",
    "RetractedandRepublishedIn": "retracted",
}
_PMCID_VERSION = re.compile(r"PMC[0-9]+\.[0-9]+", re.ASCII)


class PmcError(PubMedError):
    """Base exception for PMC failures. A ``PubMedError``, so NCBI errors share one family."""


class PmcRequestValidationError(PmcError, ValueError):
    """Raised when a PMC request is invalid before anything is sent."""


class PmcTimeoutError(PmcError):
    """Raised when a PMC request times out."""


class PmcTransportError(PmcError):
    """Raised when a PMC service cannot be reached."""


class PmcHttpError(PmcError):
    """Raised when a PMC service returns an HTTP error status."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"PMC returned HTTP status {status_code}.")


class PmcResponseError(PmcError):
    """Raised when a PMC service returns malformed or unusable data."""


@dataclass(frozen=True)
class ArticleIds:
    """Identifiers of one article according to the PMC ID converter."""

    requested_id: str
    pmcid: str
    pmid: str | None
    doi: str | None
    # For example ``PMC10009402.1``; ``None`` when no version is marked current.
    current_version: str | None


@dataclass(frozen=True)
class CloudMetadata:
    """Licence and status of one article version in the PMC Cloud Service."""

    pmcid_version: str
    pmid: str | None
    doi: str | None
    title: str | None
    citation: str | None
    # ``license_code`` exactly as received, such as ``CC BY``; may be ``None``.
    licence: str | None
    is_open_access: bool
    is_retracted: bool
    is_manuscript: bool


PMC_FETCH_ERRORS = FetchErrors(
    timeout=lambda message: PmcTimeoutError(message),
    transport=lambda message: PmcTransportError(message),
    http_status=lambda status: PmcHttpError(status),
)


class PmcClient:
    """Find PMC Open Access articles and fetch their licence data and XML."""

    def __init__(
        self,
        config: PubMedClientConfig,
        *,
        transport: HttpTransport | None = None,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self._config = config
        common = dict(
            service_name="PMC",
            errors=PMC_FETCH_ERRORS,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
            transport=transport,
            clock=clock,
            sleeper=sleeper,
        )
        # Both NCBI hosts share the PubMed client's process-wide limit.
        self._ncbi = HttpFetcher(
            rate_limit_scope=ncbi_rate_limit_scope(config.api_key),
            requests_per_second=config.requests_per_second,
            **common,
        )
        self._cloud = HttpFetcher(
            rate_limit_scope="pmc-cloud",
            requests_per_second=CLOUD_REQUESTS_PER_SECOND,
            **common,
        )

    def search_open_access(self, query: str, *, max_results: int = 20) -> tuple[str, ...]:
        """Return PMCIDs of open-access PMC articles matching plain-text terms."""
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= MAX_SEARCH_RESULTS
        ):
            raise PmcRequestValidationError(
                f"max_results must be between 1 and {MAX_SEARCH_RESULTS}."
            )
        try:
            terms = _normalise_plain_text_query(query)
        except PubMedError as error:
            raise PmcRequestValidationError(str(error)) from None
        if not terms:
            raise PmcRequestValidationError("query must be non-empty text.")
        root = self._xml(self._get(
            EUTILS_URL + "esearch.fcgi",
            {
                "db": "pmc",
                "term": f"({terms}) AND open access[filter]",
                "retmax": str(max_results),
                "retmode": "xml",
            },
        ))
        try:
            _raise_for_api_error(root)
        except PubMedError:
            raise PmcResponseError("PMC search reported an API error.") from None
        ids = [element.text or "" for element in root.findall("IdList/Id")]
        if any(not value.isascii() or not value.isdigit() for value in ids):
            raise PmcResponseError("PMC search returned an invalid article ID.")
        return tuple(f"PMC{value}" for value in ids)

    def convert_ids(
        self, ids: Iterable[str], *, id_type: str
    ) -> dict[str, ArticleIds]:
        """Look up identifiers of one type, keyed by the ID as requested.

        IDs with no PMC article (unknown, or in PubMed only) are absent from
        the result.
        """
        if id_type not in ID_TYPES:
            raise PmcRequestValidationError(
                "id_type must be one of: " + ", ".join(ID_TYPES) + "."
            )
        unique = list(dict.fromkeys(_check_id(value, id_type) for value in ids))
        found: dict[str, ArticleIds] = {}
        for start in range(0, len(unique), ID_CONVERTER_BATCH_SIZE):
            batch = unique[start : start + ID_CONVERTER_BATCH_SIZE]
            response = self._get(
                ID_CONVERTER_URL,
                {
                    "ids": ",".join(batch),
                    "idtype": id_type,
                    "versions": "yes",
                    "format": "json",
                },
            )
            for record in _converter_records(response):
                ids_found = _article_ids(record)
                if ids_found is not None:
                    found[ids_found.requested_id] = ids_found
        return found

    def pubmed_notices(self, pmids: Iterable[str]) -> dict[str, tuple[str, ...]]:
        """Return PubMed's correction and retraction notices, keyed by PMID.

        Values are flags from ``PUBMED_NOTICE_TYPES``, sorted; PMIDs with no
        notices are absent. Reads PubMed EFetch XML, 200 PMIDs per request.
        """
        unique = list(dict.fromkeys(_check_id(value, "pmid") for value in pmids))
        found: dict[str, tuple[str, ...]] = {}
        for start in range(0, len(unique), ID_CONVERTER_BATCH_SIZE):
            batch = unique[start : start + ID_CONVERTER_BATCH_SIZE]
            root = self._xml(self._get(
                EUTILS_URL + "efetch.fcgi",
                {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"},
            ))
            try:
                _raise_for_api_error(root)
            except PubMedError:
                raise PmcResponseError("PubMed reported an API error.") from None
            for article in root.findall("PubmedArticle"):
                pmid = (article.findtext("MedlineCitation/PMID") or "").strip()
                flags = {
                    PUBMED_NOTICE_TYPES[link.get("RefType", "")]
                    for link in article.iterfind(
                        "MedlineCitation/CommentsCorrectionsList/CommentsCorrections"
                    )
                    if link.get("RefType") in PUBMED_NOTICE_TYPES
                }
                if pmid in batch and flags:
                    found[pmid] = tuple(sorted(flags))
        return found

    def cloud_metadata(self, pmcid_version: str) -> CloudMetadata | None:
        """Return the Cloud metadata of a version, or ``None`` if not in the bucket."""
        version = _check_version(pmcid_version)
        try:
            response = self._get(f"{CLOUD_URL}metadata/{version}.json")
        except PmcHttpError as error:
            if error.status_code == 404:
                return None
            raise
        return _cloud_metadata(response, version)

    def article_xml(self, pmcid_version: str) -> bytes:
        """Return the JATS XML of one article version from the Cloud Service."""
        return self._get(article_xml_url(pmcid_version))

    def _get(self, url: str, parameters: dict[str, str] | None = None) -> bytes:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc not in ALLOWED_HOSTS:
            raise PmcRequestValidationError("URL is not on the PMC host allowlist.")
        if parts.netloc in _NCBI_HOSTS:
            parameters = {
                **(parameters or {}),
                "tool": self._config.tool,
                "email": self._config.email,
            }
            if self._config.api_key and parts.netloc == "eutils.ncbi.nlm.nih.gov":
                parameters["api_key"] = self._config.api_key
            fetcher = self._ncbi
        else:
            fetcher = self._cloud
        if parameters:
            url = f"{url}?{urlencode(parameters)}"
        return fetcher.get(url)

    @staticmethod
    def _xml(response: bytes) -> ElementTree.Element:
        try:
            return ElementTree.fromstring(response)
        except ElementTree.ParseError:
            raise PmcResponseError("PMC returned malformed XML.") from None


def article_xml_url(pmcid_version: str) -> str:
    """Return the Cloud Service URL of a version's JATS XML."""
    version = _check_version(pmcid_version)
    return f"{CLOUD_URL}{version}/{version}.xml"


def article_page_url(pmcid: str) -> str:
    """Return the public PMC web page of an article."""
    return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid.split('.')[0]}/"


def _check_version(value: object) -> str:
    if not isinstance(value, str) or not _PMCID_VERSION.fullmatch(value.strip()):
        raise PmcRequestValidationError(
            "pmcid_version must look like PMC12345.1."
        )
    return value.strip()


def _check_id(value: object, id_type: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PmcRequestValidationError("Every ID must be non-empty text.")
    text = value.strip()
    if id_type == "pmid":
        if not (text.isascii() and text.isdigit()):
            raise PmcRequestValidationError("PMIDs must contain digits only.")
        return strip_pmid_leading_zeros(text)
    if id_type == "pmcid":
        number = text.upper().removeprefix("PMC")
        if not (number.isascii() and number.isdigit()):
            raise PmcRequestValidationError("PMCIDs must be PMC followed by digits.")
        return "PMC" + strip_pmid_leading_zeros(number)
    doi = strip_doi_prefix(text)
    if "," in doi or not re.fullmatch(r"10\.[0-9]{4,9}/\S+", doi, re.ASCII):
        raise PmcRequestValidationError("DOIs must look like 10.1234/abc.")
    return doi


def _converter_records(response: bytes) -> Sequence[dict]:
    try:
        payload = json.loads(response)
    except (ValueError, UnicodeDecodeError):
        raise PmcResponseError("PMC ID converter returned malformed JSON.") from None
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise PmcResponseError("PMC ID converter reported an error.")
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise PmcResponseError("PMC ID converter returned no record list.")
    return [record for record in records if isinstance(record, dict)]


def _article_ids(record: dict) -> ArticleIds | None:
    requested = record.get("requested-id")
    if not isinstance(requested, str) or not requested:
        return None
    pmcid = record.get("pmcid")
    if not isinstance(pmcid, str) or not pmcid:
        return None
    current = None
    for version in record.get("versions") or ():
        if (
            isinstance(version, dict)
            and version.get("current") is True
            and isinstance(version.get("pmcid"), str)
            and _PMCID_VERSION.fullmatch(version["pmcid"])
        ):
            current = version["pmcid"]
            break
    return ArticleIds(
        requested_id=requested,
        pmcid=pmcid,
        pmid=_text_or_none(record.get("pmid")),
        doi=_text_or_none(record.get("doi")),
        current_version=current,
    )


def _cloud_metadata(response: bytes, version: str) -> CloudMetadata:
    try:
        payload = json.loads(response)
    except (ValueError, UnicodeDecodeError):
        raise PmcResponseError("PMC Cloud metadata is malformed JSON.") from None
    if not isinstance(payload, dict):
        raise PmcResponseError("PMC Cloud metadata is not an object.")
    stated = f"{payload.get('pmcid')}.{payload.get('version')}"
    if stated != version:
        raise PmcResponseError("PMC Cloud metadata describes a different article.")
    return CloudMetadata(
        pmcid_version=version,
        pmid=_text_or_none(payload.get("pmid")),
        doi=_text_or_none(payload.get("doi")),
        title=_text_or_none(payload.get("title")),
        citation=_text_or_none(payload.get("citation")),
        licence=_text_or_none(payload.get("license_code")),
        # Anything but an explicit true is treated as false (the safe side).
        is_open_access=payload.get("is_pmc_openaccess") is True,
        # Anything but an explicit false is treated as retracted (the safe side).
        is_retracted=payload.get("is_retracted") is not False,
        is_manuscript=payload.get("is_manuscript") is True,
    )


def _text_or_none(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return text or None
