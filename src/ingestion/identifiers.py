"""Shared clean-up rules for paper identifiers (DOI and PMID).

The client, pipeline, and storage each validate identifiers for their own
purpose, but they must agree on which spellings mean the same paper.
"""

from __future__ import annotations

_DOI_PREFIXES = (
    "https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
    "http://dx.doi.org/", "doi:",
)


def strip_doi_prefix(doi: str) -> str:
    """Remove one leading DOI URL or ``doi:`` prefix, ignoring case."""
    lowered = doi.casefold()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            return doi[len(prefix):].strip()
    return doi


def strip_pmid_leading_zeros(pmid: str) -> str:
    """Return a digit-only PMID without leading zeros, so ``0123`` is ``123``."""
    return pmid.lstrip("0") or pmid[-1:]
