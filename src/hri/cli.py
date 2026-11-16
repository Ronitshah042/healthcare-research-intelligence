"""The ``hri`` command line: parse arguments, call services, print results.

This module contains no business logic. Everything it does goes through
``hri.services`` so a future UI or API can reuse the same functions.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from . import services


_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_USAGE = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run one ``hri`` command and return its process exit code."""
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except services.InvalidRequestError as error:
        return _fail(error, _EXIT_USAGE)
    except services.PubMedConfigurationError as error:
        return _fail(
            f"{error} Set PUBMED_NCBI_TOOL and PUBMED_NCBI_EMAIL "
            "(see .env.example).",
            _EXIT_FAILED,
        )
    except (services.PaperStorageError, services.PubMedError, OSError) as error:
        return _fail(error, _EXIT_FAILED)
    return _EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every ``hri`` command."""
    database = argparse.ArgumentParser(add_help=False)
    database.add_argument(
        "--db",
        default=argparse.SUPPRESS,
        metavar="PATH",
        help="database file (default: $HRI_DB_PATH, else data/hri.sqlite3)",
    )
    parser = argparse.ArgumentParser(
        prog="hri",
        parents=[database],
        description="Ingest PubMed citations and search them locally.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser(
        "ingest", parents=[database], help="fetch PubMed citations and save them"
    )
    ingest.add_argument("--query", required=True, help="plain-text search terms")
    ingest.add_argument(
        "--mode",
        choices=[mode.value for mode in services.SearchMode],
        default=services.SearchMode.TITLE_ABSTRACT.value,
    )
    ingest.add_argument(
        "--max",
        type=int,
        default=services.DEFAULT_INGEST_RECORDS,
        dest="max_records",
        help=f"records to fetch, up to {services.MAX_INGEST_RECORDS}",
    )
    _add_year_arguments(ingest)
    ingest.set_defaults(handler=_ingest)

    search = commands.add_parser(
        "search", parents=[database], help="search stored citations"
    )
    search.add_argument("query")
    search.add_argument(
        "--type", choices=services.SEARCH_STRATEGIES, default="keyword", dest="strategy"
    )
    _add_year_arguments(search)
    search.add_argument(
        "--abstract",
        choices=("yes", "no"),
        help="keep only papers with (yes) or without (no) an abstract on PubMed",
    )
    search.add_argument("--page", type=int, default=1)
    search.add_argument(
        "--page-size", type=int, default=services.DEFAULT_PAGE_SIZE
    )
    search.set_defaults(handler=_search)

    show = commands.add_parser(
        "show", parents=[database], help="show one stored paper"
    )
    show.add_argument("identifier", help="PMID or DOI")
    show.set_defaults(handler=_show)

    stats = commands.add_parser(
        "stats", parents=[database], help="count stored papers"
    )
    stats.set_defaults(handler=_stats)

    lists = commands.add_parser("lists", help="manage saved lists of papers")
    list_commands = lists.add_subparsers(dest="list_command", required=True)
    create = list_commands.add_parser("create", parents=[database])
    create.add_argument("name")
    create.set_defaults(handler=_lists_create)
    for command, handler in (("add", _lists_add), ("remove", _lists_remove)):
        change = list_commands.add_parser(command, parents=[database])
        change.add_argument("name")
        change.add_argument("identifier", help="PMID or DOI")
        change.set_defaults(handler=handler)
    show_list = list_commands.add_parser("show", parents=[database])
    show_list.add_argument("name")
    show_list.set_defaults(handler=_lists_show)
    all_lists = list_commands.add_parser("list", parents=[database])
    all_lists.set_defaults(handler=_lists_list)
    export = list_commands.add_parser("export", parents=[database])
    export.add_argument("name")
    export.add_argument(
        "--format",
        choices=services.EXPORT_FORMATS,
        default="csv",
        dest="export_format",
        help="csv, citations (Vancouver/NLM style), or plain (simple layout)",
    )
    export.add_argument(
        "--output", metavar="FILE", help="write to FILE instead of the screen"
    )
    export.set_defaults(handler=_lists_export)
    return parser


def _add_year_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--year-from", type=int, metavar="YEAR")
    parser.add_argument("--year-to", type=int, metavar="YEAR")


def _db(args: argparse.Namespace) -> str | None:
    return getattr(args, "db", None)


def _ingest(args: argparse.Namespace) -> None:
    result = services.ingest(
        args.query,
        mode=args.mode,
        max_records=args.max_records,
        year_from=args.year_from,
        year_to=args.year_to,
        database_path=_db(args),
    )
    print(f'Ingested "{result.query}" ({result.mode.value}) from PubMed')
    for label, value in (
        ("PubMed matches", result.total_available),
        ("Fetched", result.fetched),
        ("Saved", result.saved),
        ("Duplicates", result.duplicates),
        ("Invalid skipped", result.invalid_skipped),
        ("Conflicts skipped", result.conflicts),
    ):
        print(f"  {label + ':':<19}{value}")
    if result.fallback_used:
        print("  No title matches; searched titles and abstracts instead.")
    _print_attribution(result.attribution)


def _search(args: argparse.Namespace) -> None:
    results = services.search_papers(
        args.query,
        strategy=args.strategy,
        page=args.page,
        page_size=args.page_size,
        year_from=args.year_from,
        year_to=args.year_to,
        abstract_available={"yes": True, "no": False, None: None}[args.abstract],
        database_path=_db(args),
    )
    print(
        f'{results.total_count} matches for "{results.query}" '
        f"({results.strategy}); page {results.page} of {results.total_pages}"
    )
    first = (results.page - 1) * results.page_size + 1
    for number, paper in enumerate(results.papers, start=first):
        print()
        _print_summary(paper, number)
    _print_attribution(results.attribution)


def _show(args: argparse.Namespace) -> None:
    paper = services.get_paper(args.identifier, database_path=_db(args))
    for label, value in (
        ("Title", paper.title),
        ("Authors", "; ".join(paper.authors) or "(none listed)"),
        ("Journal", paper.journal),
        ("Year", paper.publication_year),
        ("PMID", paper.pmid),
        ("DOI", paper.doi),
        ("Abstract", "available on PubMed" if paper.abstract_available else "none"),
        ("Source", paper.source_name),
        ("Source ID", paper.source_identifier),
        ("Link", paper.pubmed_url or paper.source_url),
    ):
        print(f"{label + ':':<11}{'-' if value is None else value}")
    _print_attribution(paper.attribution)


def _stats(args: argparse.Namespace) -> None:
    stats = services.get_stats(database_path=_db(args))
    print(f"Total papers:         {stats.total_papers}")
    print(f"With DOI:             {stats.with_doi}")
    print(f"Without DOI:          {stats.without_doi}")
    print(f"Abstract available:   {stats.abstract_available}")
    print(f"Abstract unavailable: {stats.abstract_unavailable}")
    if stats.papers_by_year:
        print("By year:")
    for year, count in stats.papers_by_year:
        print(f"  {'unknown' if year is None else year}: {count}")


def _lists_create(args: argparse.Namespace) -> None:
    saved = services.create_list(args.name, database_path=_db(args))
    print(f'Created list "{saved.name}".')


def _lists_add(args: argparse.Namespace) -> None:
    change = services.add_to_list(
        args.name, args.identifier, database_path=_db(args)
    )
    verb = "Added to" if change.changed else "Already in"
    print(f'{verb} "{change.saved_list.name}": {change.paper.title}')
    print(f"The list now has {change.saved_list.paper_count} papers.")


def _lists_remove(args: argparse.Namespace) -> None:
    change = services.remove_from_list(
        args.name, args.identifier, database_path=_db(args)
    )
    if change.changed:
        print(f'Removed from "{change.saved_list.name}": {change.paper.title}')
    else:
        print(f'"{change.saved_list.name}" did not contain that paper.')
    print(f"The list now has {change.saved_list.paper_count} papers.")


def _lists_show(args: argparse.Namespace) -> None:
    contents = services.get_list(args.name, database_path=_db(args))
    print(f'"{contents.saved_list.name}": {contents.saved_list.paper_count} papers')
    for number, paper in enumerate(contents.papers, start=1):
        print()
        _print_summary(paper, number)
    _print_attribution(contents.attribution)


def _lists_list(args: argparse.Namespace) -> None:
    saved_lists = services.get_lists(database_path=_db(args))
    if not saved_lists:
        print("No saved lists.")
    for saved in saved_lists:
        print(f"{saved.name} ({saved.paper_count} papers)")


def _lists_export(args: argparse.Namespace) -> None:
    export = services.export_list(
        args.name, args.export_format, database_path=_db(args)
    )
    if args.output:
        Path(args.output).write_text(export.content, encoding="utf-8")
        print(
            f'Wrote {export.paper_count} papers from "{export.list_name}" '
            f"to {args.output}"
        )
        if export.export_format == "csv":
            _print_attribution(export.attribution)
    else:
        sys.stdout.write(export.content)


def _print_summary(paper: services.StoredPaper, number: int) -> None:
    year = f" ({paper.publication_year})" if paper.publication_year else ""
    print(f"{number}. {paper.title}{year}")
    if paper.authors:
        shown = ", ".join(paper.authors[:3])
        print(f"   {shown}{', et al.' if len(paper.authors) > 3 else ''}")
    if paper.journal:
        print(f"   {paper.journal}")
    identifiers = [
        f"PMID {paper.pmid}" if paper.pmid else None,
        f"DOI {paper.doi}" if paper.doi else None,
    ]
    print("   " + " | ".join(part for part in identifiers if part))
    link = paper.pubmed_url or paper.source_url
    if link:
        print(f"   {link}")


def _print_attribution(attribution: services.PubMedAttribution | None) -> None:
    if attribution is None:
        return
    print()
    print(attribution.source_name)
    print(attribution.source_url)
    print(f"Disclaimer: {attribution.disclaimer_url}")
    print(attribution.independent_service_notice)


def _fail(message: object, exit_code: int) -> int:
    print(f"hri: error: {message}", file=sys.stderr)
    return exit_code
