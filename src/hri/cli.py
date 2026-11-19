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
    except services.LicencePolicyError as error:
        return _fail(f"{error} Check HRI_ALLOWED_LICENCES.", _EXIT_FAILED)
    except services.EmbedderUnavailableError as error:
        return _fail(error, _EXIT_FAILED)
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

    fulltext = commands.add_parser(
        "fetch-fulltext",
        parents=[database],
        help="store licensed open-access full text from PMC",
        description="Store PMC Open Access full text allowed by "
        "docs/CONTENT_POLICY.md (default licences: CC0, CC BY).",
    )
    source = fulltext.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--from-db", action="store_true", help="for papers already stored"
    )
    source.add_argument("--query", help="plain-text search of open-access PMC")
    fulltext.add_argument(
        "--max",
        type=int,
        default=services.DEFAULT_FULLTEXT_ARTICLES,
        dest="max_articles",
        help=f"articles to process, up to {services.MAX_FULLTEXT_ARTICLES}",
    )
    fulltext.set_defaults(handler=_fetch_fulltext)

    recheck = commands.add_parser(
        "recheck-fulltext",
        parents=[database],
        help="remove stored full text that has since been retracted",
    )
    recheck.set_defaults(handler=_recheck_fulltext)

    passages = commands.add_parser(
        "search-passages",
        parents=[database],
        help="keyword search of stored open-access full text",
        description="Find stored passages containing every word of QUERY, "
        "best match first. Each passage is shown with its attribution.",
    )
    passages.add_argument("query")
    passages.add_argument(
        "--section",
        action="append",
        choices=services.SECTION_NAMES,
        dest="sections",
        help="keep only passages from this section (repeatable)",
    )
    passages.add_argument(
        "--limit",
        type=int,
        default=services.DEFAULT_PASSAGE_RESULTS,
        help=f"passages to show, up to {services.MAX_PASSAGE_RESULTS}",
    )
    passages.set_defaults(handler=_search_passages)

    embed = commands.add_parser(
        "embed-passages",
        parents=[database],
        help="embed stored passages for meaning-based search",
        description="Download the pinned embedding model once (cached under "
        "$HRI_MODEL_CACHE, else data/models), then embed every stored passage "
        "that has no vector yet. Needs the optional embeddings install.",
    )
    embed.set_defaults(handler=_embed_passages)

    ask = commands.add_parser(
        "ask",
        parents=[database],
        help="answer a research question from stored passages, with citations",
        description="Answer QUESTION using only stored open-access passages. "
        "The question and the passages are sent to the configured AI provider "
        "(GEMINI_API_KEY, else GROQ_API_KEY). Research aid, not medical advice.",
    )
    ask.add_argument("question")
    ask.add_argument(
        "--evidence",
        type=int,
        default=services.DEFAULT_EVIDENCE,
        dest="evidence_count",
        help=f"passages to give the model, 2 to {services.MAX_EVIDENCE}",
    )
    ask.set_defaults(handler=_ask)

    evaluate = commands.add_parser(
        "eval",
        parents=[database],
        help="measure retrieval and answer quality on a question set",
        description="Run a question set (JSON), write docs/EVAL_RESULTS.md and a "
        "spot-check CSV next to the set. Uses AI requests for answers and the "
        "judge, one question every --pace seconds; cached answers are reused.",
    )
    evaluate.add_argument("--set", required=True, dest="question_set", metavar="PATH")
    evaluate.add_argument("--max-questions", type=int, metavar="N")
    evaluate.add_argument(
        "--no-answers", action="store_true", help="score retrieval only; no AI requests"
    )
    evaluate.add_argument("--report", default=str(services.DEFAULT_REPORT), metavar="PATH")
    evaluate.add_argument(
        "--pace", type=float, default=services.DEFAULT_PACE_SECONDS, metavar="SECONDS",
        help="wait after each question that used AI requests",
    )
    evaluate.add_argument("--label", default="run", help="name of this run in summaries")
    evaluate.add_argument("--summary", metavar="PATH", help="save headline numbers as JSON")
    evaluate.add_argument(
        "--baseline", metavar="PATH", help="summary JSON of an earlier run to compare with"
    )
    evaluate.add_argument("--note", help="decision text shown under the comparison")
    evaluate.add_argument(
        "--no-cache", action="store_true",
        help="ignore cached answers to measure real response time "
        f"(at most {services.MAX_NO_CACHE_QUESTIONS} questions; default 5)",
    )
    evaluate.set_defaults(handler=_eval)

    verdicts = commands.add_parser(
        "eval-verdicts",
        help="export a filled spot-check sheet without article text",
        description="Write verdicts-<date>.csv beside the sheet: question, claim, "
        "cited PMCIDs and passage IDs, judge verdict, your verdict and notes. "
        "This file can be committed; hri eval reads it for judge agreement.",
    )
    verdicts.add_argument("sheet", help="spot-check sheet, e.g. eval/spotcheck-2026-11-19.csv")
    verdicts.add_argument("--output", metavar="PATH")
    verdicts.set_defaults(handler=_eval_verdicts)

    lists = commands.add_parser("lists", help="manage saved lists of papers")
    list_commands = lists.add_subparsers(dest="list_command", required=True)
    create = list_commands.add_parser("create", parents=[database])
    create.add_argument("name")
    create.set_defaults(handler=_lists_create)
    rename = list_commands.add_parser("rename", parents=[database])
    rename.add_argument("name")
    rename.add_argument("new_name")
    rename.set_defaults(handler=_lists_rename)
    delete = list_commands.add_parser(
        "delete", parents=[database], help="delete a list; its papers stay stored"
    )
    delete.add_argument("name")
    delete.set_defaults(handler=_lists_delete)
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
    fulltext = services.get_fulltext_stats(database_path=_db(args))
    print(f"Full-text articles:   {fulltext.documents}")
    print(f"Full-text passages:   {fulltext.chunks}")
    print(f"Flagged articles:     {fulltext.flagged_documents}")
    for reason, count in fulltext.rejections_by_reason:
        print(f"Rejected ({reason}): {count}")


def _fetch_fulltext(args: argparse.Namespace) -> None:
    result = services.fetch_fulltext(
        from_db=args.from_db,
        query=args.query,
        max_articles=args.max_articles,
        database_path=_db(args),
    )
    source = "stored papers" if result.source == "database" else f'"{result.query}"'
    print(f"Fetched open-access full text for {source}")
    rows = [
        ("Candidates", result.candidates),
        ("Stored", result.stored),
        ("Already stored", result.already_stored),
        ("Rejected (licence)", result.rejected_licence),
        ("Rejected (retracted)", result.rejected_retracted),
        ("Not in OA subset", result.not_in_open_access),
        ("  of which current version unavailable", result.current_version_unavailable),
        ("Failed to parse", result.failed),
        ("Flagged (corrections/concerns)", result.flagged),
    ]
    if result.source == "database":
        rows.append(("Papers not in PMC", result.not_in_pmc))
    width = max(len(label) for label, _ in rows) + 2
    for label, value in rows:
        print(f"  {label + ':':<{width}}{value}")
    for rejection in result.rejections:
        licence = f" ({rejection.licence})" if rejection.licence else ""
        print(f"  rejected {rejection.pmcid}: {rejection.reason}{licence}")
    if result.stored:
        print("Every stored article keeps its licence and attribution text.")


def _recheck_fulltext(args: argparse.Namespace) -> None:
    result = services.recheck_fulltext(database_path=_db(args))
    print(f"Re-checked {result.checked} stored full-text articles")
    print(f"  Removed (now retracted):  {len(result.removed_retracted)}")
    print(f"  Newer version available:  {len(result.newer_version_available)}")
    print(f"  No longer in PMC Cloud:   {len(result.unavailable)}")
    print(f"  Newly flagged:            {len(result.newly_flagged)}")
    for label, pmcids in (
        ("removed", result.removed_retracted),
        ("newer version", result.newer_version_available),
        ("unavailable", result.unavailable),
        ("newly flagged", result.newly_flagged),
    ):
        for pmcid in pmcids:
            print(f"  {label}: {pmcid}")


def _search_passages(args: argparse.Namespace) -> None:
    result = services.search_passages(
        args.query,
        limit=args.limit,
        sections=args.sections or (),
        database_path=_db(args),
    )
    matches = result.matches
    print(f"Search method: {result.method}")
    if result.notice:
        print(result.notice)
    print()
    if not matches:
        print("No stored passages match.")
        return
    for number, match in enumerate(matches, start=1):
        document = match.document
        print(
            f"{number}. [{match.chunk.section_name}] {document.title} "
            f"({document.pmcid_version}, score {match.score:.2f})"
        )
        identifiers = [
            f"PMID {document.pmid}" if document.pmid else None,
            f"DOI {document.doi}" if document.doi else None,
        ]
        if any(identifiers):
            print("   " + " | ".join(part for part in identifiers if part))
        if document.notice_flags:
            print(f"   Notice: {', '.join(document.notice_flags)}")
        print()
        print(f"   {match.chunk.text}")
        print()
        print(f"   {document.attribution}")
        print()


def _embed_passages(args: argparse.Namespace) -> None:
    result = services.embed_passages(database_path=_db(args))
    print(f"Embedded {result.embedded} passages with {result.model_id}")
    print(
        f"  Longer than the model's limit (cut off): {result.over_token_limit}"
        f" (longest {result.longest_tokens} tokens)"
    )


def _ask(args: argparse.Namespace) -> None:
    answer = services.ask_question(
        args.question, evidence_count=args.evidence_count, database_path=_db(args)
    )
    print(answer.text)
    if answer.abstained:
        print(f"\n(No answer given: {answer.abstain_reason.replace('_', ' ')}.)")
    if answer.citations:
        print("\nSources:")
        for citation in answer.citations:
            identifiers = " | ".join(
                part for part in (
                    f"PMID {citation.pmid}" if citation.pmid else None,
                    f"DOI {citation.doi}" if citation.doi else None,
                    citation.pmcid_version,
                ) if part
            )
            print(f"[{citation.number}] {citation.title} ({citation.section})")
            print(f"    {identifiers}")
            print(f"    {citation.link}")
            print(f"    {citation.attribution}")
    details = [f"search: {answer.search_method}"]
    if answer.provider:
        details.append(f"model: {answer.provider} {answer.model}")
    if answer.cached:
        details.append("cached answer")
    if answer.invalid_citations:
        details.append(f"{answer.invalid_citations} invalid citations removed")
    print("\n" + "; ".join(details))
    if answer.notice:
        print(answer.notice)
    print(answer.disclaimer)


def _eval(args: argparse.Namespace) -> None:
    result = services.run_eval(
        args.question_set,
        database_path=_db(args),
        max_questions=args.max_questions,
        answers=not args.no_answers,
        report_path=args.report,
        pace_seconds=args.pace,
        label=args.label,
        summary_path=args.summary,
        baseline_path=args.baseline,
        note=args.note,
        no_cache=args.no_cache,
    )
    print(f"Evaluated {result.questions} questions; AI requests used: {result.llm_calls}")
    for method, values in result.retrieval.items():
        print(f"  {method:<8} recall@5 {values['recall@5']:.0%}  MRR {values['MRR']:.2f}")
    print(f"Report: {result.report_path}")
    if result.spotcheck_path:
        print(f"Spot-check sheet: {result.spotcheck_path}")


def _eval_verdicts(args: argparse.Namespace) -> None:
    output = services.export_verdicts(args.sheet, args.output)
    print(f"Verdicts without article text: {output}")


def _lists_create(args: argparse.Namespace) -> None:
    saved = services.create_list(args.name, database_path=_db(args))
    print(f'Created list "{saved.name}".')


def _lists_rename(args: argparse.Namespace) -> None:
    saved = services.rename_list(args.name, args.new_name, database_path=_db(args))
    print(f'Renamed list to "{saved.name}" ({saved.paper_count} papers).')


def _lists_delete(args: argparse.Namespace) -> None:
    saved = services.delete_list(args.name, database_path=_db(args))
    print(f'Deleted list "{saved.name}" ({saved.paper_count} papers).')
    print("The papers themselves are still stored.")


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
