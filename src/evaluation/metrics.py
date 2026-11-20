"""Evaluation metrics as pure functions over plain values.

Retrieval is scored per article: a ranked passage list is first reduced to
the order in which distinct articles first appear.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def article_ranking(article_ids: Iterable[str]) -> list[str]:
    """Distinct article IDs in order of first appearance."""
    return list(dict.fromkeys(article_ids))


def recall_at_k(ranking: Sequence[str], relevant: set[str], k: int) -> float:
    """Share of the relevant articles found in the top ``k``."""
    if not relevant:
        raise ValueError("recall needs at least one relevant article.")
    return len(relevant.intersection(ranking[:k])) / len(relevant)


def precision_at_k(ranking: Sequence[str], relevant: set[str], k: int) -> float:
    """Share of the top ``k`` positions holding a relevant article.

    Fewer than ``k`` results still divide by ``k``: an empty slot is a miss.
    """
    if k < 1:
        raise ValueError("k must be at least 1.")
    return len(relevant.intersection(ranking[:k])) / k


def reciprocal_rank(ranking: Sequence[str], relevant: set[str]) -> float:
    """1 / position of the first relevant article; 0 when none is found."""
    for position, article in enumerate(ranking, start=1):
        if article in relevant:
            return 1.0 / position
    return 0.0


def mean(values: Iterable[float]) -> float | None:
    """Arithmetic mean, or ``None`` for no values."""
    items = list(values)
    return sum(items) / len(items) if items else None


def rate(count: int, total: int) -> float | None:
    """``count / total``, or ``None`` when ``total`` is 0."""
    return count / total if total else None


def citation_validity_rate(valid: int, invalid: int) -> float | None:
    """Share of all citations written by the model that name a supplied passage."""
    return rate(valid, valid + invalid)


def verdict_counts(verdicts: Iterable[str]) -> dict[str, int]:
    """Count judge verdicts: supported, partial, unsupported (others ignored)."""
    counts = {"supported": 0, "partial": 0, "unsupported": 0}
    for verdict in verdicts:
        if verdict in counts:
            counts[verdict] += 1
    return counts


def percentile(values: Iterable[float], fraction: float) -> float | None:
    """Nearest-rank percentile, for example ``fraction=0.9`` for p90."""
    items = sorted(values)
    if not items:
        return None
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1].")
    return items[max(0, math.ceil(fraction * len(items)) - 1)]
