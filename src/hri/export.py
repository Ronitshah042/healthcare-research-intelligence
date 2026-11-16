"""Turn stored papers into CSV text or plain-text citations."""

from __future__ import annotations

from collections.abc import Iterable
import csv
from io import StringIO

from ingestion import StoredPaper


EXPORT_FORMATS = ("csv", "citations")
CSV_COLUMNS = (
    "pmid", "doi", "title", "authors", "journal", "publication_year",
    "abstract_available", "source", "url",
)
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
                "yes" if paper.abstract_available else "no",
                paper.source_name, paper.pubmed_url or paper.source_url,
            )
        )
    return buffer.getvalue()


def papers_to_citations(papers: Iterable[StoredPaper]) -> str:
    """Return numbered plain-text citations, then any required attribution.

    This is a simple readable layout, not a formal style such as Vancouver.
    """
    papers = tuple(papers)
    lines = [
        f"{number}. {format_citation(paper)}"
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


def format_citation(paper: StoredPaper) -> str:
    """Return one citation: authors, title, journal, year, identifiers, link."""
    parts = [
        ", ".join(paper.authors),
        paper.title,
        paper.journal,
        str(paper.publication_year) if paper.publication_year else None,
        f"PMID: {paper.pmid}" if paper.pmid else None,
        f"doi:{paper.doi}" if paper.doi else None,
    ]
    text = ". ".join(part.rstrip(". ") for part in parts if part) + "."
    link = paper.pubmed_url or paper.source_url
    return f"{text} {link}" if link else text


def _safe_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return "" if value is None else value
