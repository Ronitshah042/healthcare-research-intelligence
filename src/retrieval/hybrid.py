"""Hybrid passage search: BM25 keyword ranks fused with embedding ranks."""

from __future__ import annotations

from dataclasses import replace

from ingestion.fulltext_store import FullTextStore, PassageMatch

from .embeddings import Embedder, from_blob


# Candidates taken from each ranking before fusing.
CANDIDATES = 50
# Reciprocal rank fusion constant; 60 is the value from the original paper.
RRF_K = 60


def vector_search(
    store: FullTextStore,
    embedder: Embedder,
    query: str,
    *,
    limit: int,
    sections: tuple[str, ...] = (),
) -> tuple[PassageMatch, ...]:
    """Return the passages closest in meaning to ``query``, best first.

    ``score`` is cosine similarity (vectors are unit length).
    """
    # shortcut: compares against every stored vector in pure Python, fine to
    # ~50,000 passages; past that, use a vector index such as sqlite-vec.
    query_vector = embedder.embed_query(query)
    scored = sorted(
        (
            (sum(a * b for a, b in zip(query_vector, from_blob(blob))), chunk_id)
            for chunk_id, blob in store.embeddings(embedder.model_id, sections=sections)
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )[:limit]
    passages = store.passages(chunk_id for _, chunk_id in scored)
    return tuple(
        replace(passages[chunk_id], score=score)
        for score, chunk_id in scored
        if chunk_id in passages
    )


def hybrid_search(
    store: FullTextStore,
    embedder: Embedder,
    query: str,
    *,
    limit: int,
    sections: tuple[str, ...] = (),
) -> tuple[PassageMatch, ...]:
    """Fuse keyword and meaning-based rankings with reciprocal rank fusion.

    A passage scores ``sum(1 / (RRF_K + rank))`` over the rankings it appears
    in, so one found by both methods beats one found by a single method.
    Raises ``ValueError`` if ``query`` has no words, like keyword search.
    """
    keyword = store.search(query, limit=CANDIDATES, sections=sections)
    meaning = vector_search(store, embedder, query, limit=CANDIDATES, sections=sections)
    return fuse(keyword, meaning, limit=limit)


def fuse(
    *rankings: tuple[PassageMatch, ...], limit: int
) -> tuple[PassageMatch, ...]:
    """Reciprocal rank fusion of rankings, best first; ``score`` is the fused score."""
    fused: dict[tuple[str, int], float] = {}
    matches: dict[tuple[str, int], PassageMatch] = {}
    for ranking in rankings:
        for rank, match in enumerate(ranking, start=1):
            key = (match.chunk.pmcid, match.chunk.position)
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
            matches.setdefault(key, match)
    best = sorted(fused, key=lambda key: (-fused[key], key))[:limit]
    return tuple(replace(matches[key], score=fused[key]) for key in best)
