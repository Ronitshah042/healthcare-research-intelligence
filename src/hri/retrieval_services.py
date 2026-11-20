"""UI-agnostic passage search and embedding over stored open-access full text.

Re-exported by ``hri.services``; the contract is in ``docs/SERVICE_API.md``.
Meaning-based search is optional: whenever the embedding model cannot be
used, search falls back to keyword-only and says why in ``notice``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ingestion.fulltext import SECTION_NAMES
from ingestion.fulltext_store import FullTextStore, PassageMatch
from retrieval.embeddings import (
    MODEL_ID,
    MODEL_MAX_TOKENS,
    Embedder,
    EmbedderUnavailableError,
    FastEmbedder,
    model_cache_dir,
    prepare,
    to_blob,
)
from retrieval.hybrid import hybrid_search

from .config import resolve_database_path
from .errors import InvalidRequestError, as_invalid_request

LOGGER = logging.getLogger(__name__)
DEFAULT_PASSAGE_RESULTS = 10
MAX_PASSAGE_RESULTS = 50
SEARCH_METHODS = ("hybrid", "keyword")
_EMBED_BATCH = 32
# One loaded model per process and cache folder, so it is read from disk once.
_LOADED: dict[Path, Embedder] = {}

__all__ = [
    "DEFAULT_PASSAGE_RESULTS",
    "MAX_PASSAGE_RESULTS",
    "SECTION_NAMES",
    "SEARCH_METHODS",
    "EmbedderUnavailableError",
    "EmbedderStatus",
    "EmbedPassagesResult",
    "PassageMatch",
    "PassageSearchResult",
    "prepare_embedder",
    "embed_passages",
    "search_passages",
]


@dataclass(frozen=True)
class EmbedderStatus:
    """Whether meaning-based search can run, and if not, why."""

    available: bool
    model_id: str
    # ``None`` when available; otherwise a sentence a user can act on.
    message: str | None


@dataclass(frozen=True)
class EmbedPassagesResult:
    """What one embedding run did."""

    model_id: str
    embedded: int
    # Passages embedded in this run that were longer than the model's limit,
    # so only their first ``MODEL_MAX_TOKENS`` tokens were used.
    over_token_limit: int
    longest_tokens: int


@dataclass(frozen=True)
class PassageSearchResult:
    """Passages for one search, and how they were found."""

    matches: tuple[PassageMatch, ...]
    # "hybrid" (keyword and meaning) or "keyword".
    method: str
    # Why the search was keyword-only, or a passage-coverage warning; else None.
    notice: str | None


def prepare_embedder(*, download: bool = True) -> EmbedderStatus:
    """Download (once) and load the pinned embedding model.

    Call this at application start-up. With ``download=False`` nothing is
    fetched: only an already-downloaded model is loaded. Never raises for an
    unavailable model; the status says what is wrong.
    """
    cache = model_cache_dir()
    if cache in _LOADED:
        return EmbedderStatus(True, MODEL_ID, None)
    try:
        if download:
            prepare(cache)
        _LOADED[cache] = FastEmbedder(cache)
    except EmbedderUnavailableError as error:
        LOGGER.warning("Embedding search unavailable: %s", error)
        return EmbedderStatus(False, MODEL_ID, str(error))
    return EmbedderStatus(True, MODEL_ID, None)


def loaded_embedder() -> tuple[Embedder | None, str | None]:
    """Return the already-downloaded model, or ``None`` and why. Never downloads."""
    status = prepare_embedder(download=False)
    return _LOADED.get(model_cache_dir()), status.message


def embed_passages(
    *,
    database_path: str | Path | None = None,
    embedder: Embedder | None = None,
) -> EmbedPassagesResult:
    """Embed every stored passage that has no vector from the model yet.

    Downloads the model first if needed. Raises ``EmbedderUnavailableError``
    when the model cannot be used.
    """
    if embedder is None:
        status = prepare_embedder()
        if not status.available:
            raise EmbedderUnavailableError(status.message)
        embedder = _LOADED[model_cache_dir()]
    store = FullTextStore(resolve_database_path(database_path))
    embedded = over_limit = longest = 0
    while True:
        batch = store.chunks_to_embed(embedder.model_id, limit=_EMBED_BATCH)
        if not batch:
            break
        texts = [text for _, text in batch]
        lengths = [embedder.count_tokens(text) for text in texts]
        vectors = embedder.embed_passages(texts)
        store.save_embeddings(
            embedder.model_id,
            [(chunk_id, to_blob(vector)) for (chunk_id, _), vector in zip(batch, vectors)],
        )
        embedded += len(batch)
        over_limit += sum(length > MODEL_MAX_TOKENS for length in lengths)
        longest = max([longest, *lengths])
    LOGGER.info(
        "Embedded %d passages with %s; %d exceeded %d tokens and were cut off "
        "(longest %d).",
        embedded, embedder.model_id, over_limit, MODEL_MAX_TOKENS, longest,
    )
    return EmbedPassagesResult(embedder.model_id, embedded, over_limit, longest)


def search_passages(
    query: str,
    *,
    limit: int = DEFAULT_PASSAGE_RESULTS,
    sections: Iterable[str] = (),
    database_path: str | Path | None = None,
    embedder: Embedder | None = None,
) -> PassageSearchResult:
    """Search stored passages, hybrid when possible, best match first.

    Never downloads anything: only an already-prepared model is used. Each
    match carries its document, whose ``attribution`` must be shown with the
    passage text (CONTENT_POLICY.md).
    """
    if not isinstance(query, str):
        raise InvalidRequestError("query must be text.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not (
        1 <= limit <= MAX_PASSAGE_RESULTS
    ):
        raise InvalidRequestError(
            f"limit must be between 1 and {MAX_PASSAGE_RESULTS}."
        )
    wanted = tuple(dict.fromkeys(section.strip().lower() for section in sections))
    unknown = [section for section in wanted if section not in SECTION_NAMES]
    if unknown:
        raise InvalidRequestError(
            f"Unknown section {unknown[0]!r}; use one of: {', '.join(SECTION_NAMES)}."
        )
    store = FullTextStore(resolve_database_path(database_path))
    notice = None
    if embedder is None:
        embedder, notice = loaded_embedder()
    with as_invalid_request():
        if embedder is None:
            matches = store.search(query, limit=limit, sections=wanted)
            return PassageSearchResult(
                matches, "keyword", f"Keyword-only search: {notice}"
            )
        matches = hybrid_search(store, embedder, query, limit=limit, sections=wanted)
    missing = store.count_unembedded(embedder.model_id)
    if missing:
        notice = (
            f"{missing} passages have no embedding yet and were matched by "
            "keyword only; run `hri embed-passages`."
        )
    return PassageSearchResult(matches, "hybrid", notice)
