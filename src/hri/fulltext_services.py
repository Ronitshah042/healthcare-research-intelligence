"""UI-agnostic operations for licensed open-access full text.

Re-exported by ``hri.services``; the contract is in ``docs/SERVICE_API.md``
and the rules in ``docs/CONTENT_POLICY.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ingestion import PaperStore, PubMedClientConfig
from ingestion.chunking import chunk_sections
from ingestion.fulltext import FullTextParseError, parse_article
from ingestion.fulltext_store import (
    FullTextDocument,
    FullTextRejection,
    FullTextStats,
    FullTextStore,
)
from ingestion.licence_policy import LicencePolicy, LicencePolicyError
from ingestion.pmc_client import (
    MAX_SEARCH_RESULTS,
    ArticleIds,
    PmcClient,
    article_page_url,
    article_xml_url,
)

from .config import resolve_database_path
from .errors import InvalidRequestError, as_invalid_request

LOGGER = logging.getLogger(__name__)
DEFAULT_FULLTEXT_ARTICLES = 20
MAX_FULLTEXT_ARTICLES = 200
_ATTRIBUTION_AUTHORS = 6

__all__ = [
    "DEFAULT_FULLTEXT_ARTICLES",
    "MAX_FULLTEXT_ARTICLES",
    "FullTextDocument",
    "FullTextRejection",
    "FullTextStats",
    "FullTextFetchResult",
    "FullTextRecheckResult",
    "LicencePolicyError",
    "fetch_fulltext",
    "recheck_fulltext",
    "get_fulltext_stats",
]


@dataclass(frozen=True)
class FullTextFetchResult:
    """What happened to each candidate article in one fetch run."""

    # "database" or "query".
    source: str
    query: str | None
    # Articles with a PMC record that were considered, at most the limit.
    candidates: int
    stored: int
    # The current version was already stored, so nothing was fetched.
    already_stored: int
    rejected_licence: int
    rejected_retracted: int
    # Not in the Open Access subset, including ``current_version_unavailable``.
    not_in_open_access: int
    # The subset of ``not_in_open_access`` whose current version is missing.
    current_version_unavailable: int
    # Stored papers with no PMC article at all (``source == "database"`` only).
    not_in_pmc: int
    # Allowed articles whose XML could not be parsed; nothing was stored.
    failed: int
    # Stored articles that carry correction or concern flags.
    flagged: int
    rejections: tuple[FullTextRejection, ...]


@dataclass(frozen=True)
class FullTextRecheckResult:
    """The outcome of re-checking every stored article's status."""

    checked: int
    # PMCIDs deleted because they are now retracted.
    removed_retracted: tuple[str, ...]
    # PMCIDs whose stored version is no longer the current one.
    newer_version_available: tuple[str, ...]
    # PMCIDs whose stored version has left the Cloud Service; text is kept.
    unavailable: tuple[str, ...]
    # PMCIDs that gained notice flags from PubMed's correction links.
    newly_flagged: tuple[str, ...]


def fetch_fulltext(
    *,
    from_db: bool = False,
    query: str | None = None,
    max_articles: int = DEFAULT_FULLTEXT_ARTICLES,
    database_path: str | Path | None = None,
    client: PmcClient | None = None,
    policy: LicencePolicy | None = None,
    now: Callable[[], datetime] | None = None,
) -> FullTextFetchResult:
    """Store allowed open-access full text for stored papers or a PMC query."""
    if from_db == (query is not None):
        raise InvalidRequestError("Give exactly one of from_db=True or a query.")
    if (
        isinstance(max_articles, bool)
        or not isinstance(max_articles, int)
        or not 1 <= max_articles <= MAX_FULLTEXT_ARTICLES
    ):
        raise InvalidRequestError(
            f"max_articles must be between 1 and {MAX_FULLTEXT_ARTICLES}."
        )
    policy = policy if policy is not None else LicencePolicy.from_environment()
    pmc = client if client is not None else build_pmc_client()
    path = resolve_database_path(database_path)
    store = FullTextStore(path)
    stored_versions = {doc.pmcid: doc.pmcid_version for doc in store.documents()}

    not_in_pmc = 0
    with as_invalid_request():
        if from_db:
            candidates, not_in_pmc = _stored_paper_articles(pmc, PaperStore(path))
        else:
            # Search wider than the limit so repeated runs move past articles
            # already stored; this is still a single request.
            pmcids = pmc.search_open_access(query, max_results=MAX_SEARCH_RESULTS)
            converted = pmc.convert_ids(pmcids, id_type="pmcid")
            candidates = [converted[pmcid] for pmcid in pmcids if pmcid in converted]

    counts = dict.fromkeys(
        ("stored", "already_stored", "failed", "flagged"), 0
    )
    rejections: list[FullTextRejection] = []
    considered = 0
    with as_invalid_request():
        notices = pmc.pubmed_notices(ids.pmid for ids in candidates if ids.pmid)
    for ids in candidates:
        if ids.current_version and stored_versions.get(ids.pmcid) == ids.current_version:
            counts["already_stored"] += 1
            continue
        if considered == max_articles:
            break
        considered += 1
        outcome = _fetch_one(
            pmc, store, policy, ids, notices.get(ids.pmid or "", ()), now or _utc_now
        )
        if isinstance(outcome, FullTextRejection):
            rejections.append(outcome)
        else:
            counts[outcome] += 1
            if outcome == "flagged":
                counts["stored"] += 1

    reasons = [rejection.reason for rejection in rejections]
    return FullTextFetchResult(
        source="database" if from_db else "query",
        query=query,
        candidates=considered + counts["already_stored"],
        stored=counts["stored"],
        already_stored=counts["already_stored"],
        rejected_licence=reasons.count("licence"),
        rejected_retracted=reasons.count("retracted"),
        not_in_open_access=reasons.count("not_open_access")
        + reasons.count("current_version_unavailable"),
        current_version_unavailable=reasons.count("current_version_unavailable"),
        not_in_pmc=not_in_pmc,
        failed=counts["failed"],
        flagged=counts["flagged"],
        rejections=tuple(rejections),
    )


def recheck_fulltext(
    *,
    database_path: str | Path | None = None,
    client: PmcClient | None = None,
    now: Callable[[], datetime] | None = None,
) -> FullTextRecheckResult:
    """Re-read the status of every stored article and remove retracted ones.

    Run this before every rebuild of a data bundle (CONTENT_POLICY.md 7).
    """
    pmc = client if client is not None else build_pmc_client()
    store = FullTextStore(resolve_database_path(database_path))
    documents = store.documents()
    current = pmc.convert_ids([doc.pmcid for doc in documents], id_type="pmcid")
    notices = pmc.pubmed_notices(doc.pmid for doc in documents if doc.pmid)
    removed: list[str] = []
    flagged: list[str] = []
    newer: list[str] = []
    unavailable: list[str] = []
    for document in documents:
        versions = [document.pmcid_version]
        current_version = current.get(document.pmcid)
        if current_version and current_version.current_version not in (
            None, document.pmcid_version
        ):
            versions.append(current_version.current_version)
            newer.append(document.pmcid)
        statuses = [pmc.cloud_metadata(version) for version in versions]
        if statuses[0] is None:
            unavailable.append(document.pmcid)
        pubmed_flags = notices.get(document.pmid or "", ())
        if "retracted" in pubmed_flags or any(
            status is not None and status.is_retracted for status in statuses
        ):
            store.reject(
                document.pmcid, "retracted",
                recorded_at=_timestamp((now or _utc_now)()),
                pmcid_version=document.pmcid_version, licence=document.licence,
            )
            LOGGER.warning("Removed retracted PMC article %s.", document.pmcid)
            removed.append(document.pmcid)
            continue
        merged = tuple(sorted(set(document.notice_flags) | set(pubmed_flags)))
        if merged != document.notice_flags:
            store.set_notice_flags(document.pmcid, merged)
            flagged.append(document.pmcid)
    return FullTextRecheckResult(
        checked=len(documents),
        removed_retracted=tuple(removed),
        newer_version_available=tuple(pmcid for pmcid in newer if pmcid not in removed),
        unavailable=tuple(pmcid for pmcid in unavailable if pmcid not in removed),
        newly_flagged=tuple(flagged),
    )


def get_fulltext_stats(*, database_path: str | Path | None = None) -> FullTextStats:
    """Return counts of stored documents, chunks, flags, and rejections."""
    return FullTextStore(resolve_database_path(database_path)).stats()


def build_pmc_client() -> PmcClient:
    """Build the live PMC client from the ``PUBMED_*`` environment variables."""
    return PmcClient(PubMedClientConfig.from_environment())


def build_attribution(
    *,
    authors: Iterable[str],
    title: str,
    citation: str | None,
    pmcid_version: str,
    licence: str,
    licence_url: str | None,
    retrieved_at: str,
) -> str:
    """Return the credit line CC licences require, noting our changes."""
    names = list(authors)
    shown = ", ".join(names[:_ATTRIBUTION_AUTHORS])
    if len(names) > _ATTRIBUTION_AUTHORS:
        shown += ", et al."
    sentences = []
    for part in (shown, title, citation):
        text = (part or "").strip().rstrip(".")
        if text:
            sentences.append(text if text.endswith(("?", "!")) else text + ".")
    licence_text = f"{licence} ({licence_url})" if licence_url else licence
    return (
        " ".join(sentences) + " "
        f"{pmcid_version}. Licence: {licence_text}. "
        f"Source: PubMed Central, {article_page_url(pmcid_version)}, "
        f"retrieved {retrieved_at[:10]}. "
        "Changes: text extracted and split into passages; references, "
        "figures, and tables removed."
    )


def _stored_paper_articles(
    pmc: PmcClient, papers: PaperStore
) -> tuple[list[ArticleIds], int]:
    """Return PMC articles for stored papers, in storage order, and the misses."""
    stored = []
    offset = 0
    while True:
        page = papers.list_papers(limit=100, offset=offset)
        if not page:
            break
        stored.extend(page)
        offset += len(page)
    by_pmid = pmc.convert_ids(
        [paper.pmid for paper in stored if paper.pmid], id_type="pmid"
    )
    by_doi = pmc.convert_ids(
        [paper.doi for paper in stored if not paper.pmid and paper.doi], id_type="doi"
    )
    found: list[ArticleIds] = []
    seen: set[str] = set()
    misses = 0
    for paper in stored:
        ids = by_pmid.get(paper.pmid) if paper.pmid else by_doi.get(paper.doi or "")
        if ids is None:
            misses += 1
        elif ids.pmcid not in seen:
            seen.add(ids.pmcid)
            found.append(ids)
    return found, misses


def _fetch_one(
    pmc: PmcClient,
    store: FullTextStore,
    policy: LicencePolicy,
    ids: ArticleIds,
    pubmed_flags: tuple[str, ...],
    now: Callable[[], datetime],
) -> FullTextRejection | str:
    """Apply the content policy to one article; store it only if allowed."""
    version = ids.current_version

    def reject(reason: str, licence: str | None = None) -> FullTextRejection:
        LOGGER.warning("Rejected PMC article %s: %s.", ids.pmcid, reason)
        return store.reject(
            ids.pmcid, reason, recorded_at=_timestamp(now()),
            pmcid_version=version, licence=licence,
        )

    if version is None:
        return reject("current_version_unavailable")
    metadata = pmc.cloud_metadata(version)
    if metadata is None:
        return reject("current_version_unavailable")
    if not metadata.is_open_access:
        return reject("not_open_access", metadata.licence)
    if metadata.is_retracted or "retracted" in pubmed_flags:
        return reject("retracted", metadata.licence)
    if not policy.allows(metadata.licence):
        return reject("licence", metadata.licence)

    # Only now, with an allowed licence, is the article text downloaded.
    try:
        article = parse_article(pmc.article_xml(version))
    except FullTextParseError:
        LOGGER.warning("Could not parse PMC article %s.", ids.pmcid)
        return "failed"
    if article.links_to_retraction:
        return reject("retracted", metadata.licence)

    flags = tuple(sorted(set(article.notice_flags) | set(pubmed_flags)))
    retrieved_at = _timestamp(now())
    store.save(
        FullTextDocument(
            pmcid=ids.pmcid,
            pmcid_version=version,
            pmid=metadata.pmid or ids.pmid,
            doi=metadata.doi or ids.doi,
            title=article.title or metadata.title or ids.pmcid,
            licence=metadata.licence or "",
            source_url=article_xml_url(version),
            retrieved_at=retrieved_at,
            attribution=build_attribution(
                authors=article.authors,
                title=article.title or metadata.title or "",
                citation=metadata.citation,
                pmcid_version=version,
                licence=metadata.licence or "",
                licence_url=article.licence_url,
                retrieved_at=retrieved_at,
            ),
            article_type=article.article_type,
            notice_flags=flags,
        ),
        article.sections,
        chunk_sections(article.sections),
    )
    return "flagged" if flags else "stored"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
