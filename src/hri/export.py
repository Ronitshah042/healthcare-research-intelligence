"""Turn stored papers into CSV text, Vancouver citations, or a plain layout."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import csv
from io import StringIO

from ingestion import StoredPaper


# ``citations`` is Vancouver (NLM) style; ``plain`` is a simple readable layout.
EXPORT_FORMATS = ("csv", "citations", "plain")
CSV_COLUMNS = (
    "pmid", "doi", "title", "authors", "journal", "publication_year",
    "volume", "issue", "pages", "abstract_available", "source", "url",
)
# ICMJE: list the first six authors, then "et al."
_VANCOUVER_MAX_AUTHORS = 6
# Spreadsheet programs run cells starting with these characters as formulas.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def papers_to_csv(papers: Iterable[StoredPaper]) -> str:
    """Return a CSV document with a header row and one row per paper."""
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for paper in papers:
        writer.writerow(
            _safe_cell(value)
            for value in (
                paper.pmid, paper.doi, paper.title, "; ".join(paper.authors),
                paper.journal, paper.publication_year,
                paper.citation and paper.citation.volume,
                paper.citation and paper.citation.issue,
                paper.citation and paper.citation.pages,
                "yes" if paper.abstract_available else "no",
                paper.source_name, paper.pubmed_url or paper.source_url,
            )
        )
    return buffer.getvalue()


def papers_to_citations(papers: Iterable[StoredPaper]) -> str:
    """Return numbered Vancouver (NLM) citations, then any required attribution."""
    return _numbered(papers, format_vancouver)


def papers_to_plain(papers: Iterable[StoredPaper]) -> str:
    """Return numbered plain citations with links, then any attribution.

    This is a simple readable layout, not a formal citation style.
    """
    return _numbered(papers, format_plain)


def format_vancouver(paper: StoredPaper) -> str:
    """Return one citation in Vancouver (NLM) style from the stored fields.

    ``Authors. Title. Journal abbreviation. Year;Volume(Issue):Pages. doi: x.
    PMID: n.`` Parts the store does not hold are left out. Papers saved
    without citation details fall back to full author names and journal title.
    """
    citation = paper.citation
    names = (citation.authors if citation else ()) or paper.authors
    authors = ", ".join(names[:_VANCOUVER_MAX_AUTHORS])
    if len(names) > _VANCOUVER_MAX_AUTHORS:
        authors += ", et al"
    locator = ""
    if citation is not None:
        locator += citation.volume or ""
        locator += f"({citation.issue})" if citation.issue else ""
        locator += f":{citation.pages}" if citation.pages else ""
    year = str(paper.publication_year) if paper.publication_year else ""
    # NLM writes "2020;383(27):12-5", and "2020:12-5" with no volume or issue.
    separator = ";" if year and locator and not locator.startswith(":") else ""
    return _sentences(
        authors,
        paper.title,
        (citation.journal_abbreviation if citation else None) or paper.journal,
        year + separator + locator,
        f"doi: {paper.doi}" if paper.doi else None,
        f"PMID: {paper.pmid}" if paper.pmid else None,
    )


def format_plain(paper: StoredPaper) -> str:
    """Return one plain citation: authors, title, journal, year, IDs, link."""
    text = _sentences(
        ", ".join(paper.authors),
        paper.title,
        paper.journal,
        str(paper.publication_year) if paper.publication_year else None,
        f"PMID: {paper.pmid}" if paper.pmid else None,
        f"doi:{paper.doi}" if paper.doi else None,
    )
    link = paper.pubmed_url or paper.source_url
    return f"{text} {link}" if link else text


def _numbered(
    papers: Iterable[StoredPaper], formatter: Callable[[StoredPaper], str]
) -> str:
    papers = tuple(papers)
    lines = [
        f"{number}. {formatter(paper)}"
        for number, paper in enumerate(papers, start=1)
    ]
    attributions = {paper.attribution for paper in papers} - {None}
    for attribution in sorted(attributions, key=lambda item: item.source_name):
        lines += [
            "",
            attribution.source_name,
            attribution.source_url,
            f"Disclaimer: {attribution.disclaimer_url}",
            attribution.independent_service_notice,
        ]
    return "\n".join(lines) + ("\n" if lines else "")


def _sentences(*parts: str | None) -> str:
    """Join the non-empty parts, ending each with exactly one full stop."""
    sentences = []
    for part in parts:
        text = (part or "").rstrip(". ")
        if text:
            sentences.append(text if text.endswith(("?", "!")) else text + ".")
    return " ".join(sentences)


def _safe_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return "" if value is None else value
