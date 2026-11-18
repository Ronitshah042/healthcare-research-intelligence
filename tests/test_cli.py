"""Tests for the ``hri`` command line, using a fake PubMed client and temp databases."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import csv
from io import StringIO
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hri import cli, services
from ingestion import PUBMED_ATTRIBUTION, PubMedTransportError

from test_services import FakePubMedClient, _record


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp_dir.name) / "hri.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_cli(self, *argv: str, db: bool = True) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        arguments = [*argv, "--db", self.db] if db else list(argv)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def ingest(self, records, *argv: str) -> tuple[int, str, str]:
        client = FakePubMedClient(records)
        with mock.patch.object(services, "build_pubmed_client", return_value=client):
            result = self.run_cli("ingest", "--query", "sleep", *argv)
        self.client = client
        return result


class ArgumentTests(CliTestCase):
    def test_passes_ingest_arguments_to_the_service(self) -> None:
        with mock.patch.object(services, "ingest") as ingest:
            ingest.return_value = services.IngestResult(
                query="type 2 diabetes", mode=services.SearchMode.TITLE_ABSTRACT,
                requested_max=200, total_available=900, fetched=200, saved=190,
                duplicates=7, invalid_skipped=3, conflicts=1, fallback_used=False,
            )
            code, out, _ = self.run_cli(
                "ingest", "--query", "type 2 diabetes", "--mode", "title_abstract",
                "--max", "200", "--year-from", "2018",
            )

        ingest.assert_called_once_with(
            "type 2 diabetes", mode="title_abstract", max_records=200,
            year_from=2018, year_to=None, database_path=self.db,
        )
        self.assertEqual(code, 0)
        for expected in (
            "PubMed matches:    900", "Fetched:           200", "Saved:             190",
            "Duplicates:        7", "Invalid skipped:   3", "Conflicts skipped: 1",
            PUBMED_ATTRIBUTION.source_name,
        ):
            self.assertIn(expected, out)

    def test_passes_search_arguments_to_the_service(self) -> None:
        with mock.patch.object(services, "search_papers") as search:
            search.return_value = services.SearchResults(
                query="q", strategy="boolean", papers=(), total_count=0, page=1,
                page_size=20, total_pages=0, attribution=None,
            )
            self.run_cli(
                "search", "metformin AND cardiovascular", "--type", "boolean",
                "--year-from", "2020", "--page", "1", "--abstract", "yes",
            )

        search.assert_called_once_with(
            "metformin AND cardiovascular", strategy="boolean", page=1, page_size=20,
            year_from=2020, year_to=None, abstract_available=True,
            database_path=self.db,
        )

    def test_uses_defaults_when_optional_arguments_are_omitted(self) -> None:
        with mock.patch.object(services, "ingest") as ingest, \
                mock.patch.object(services, "search_papers") as search:
            search.return_value = ingest.return_value = None
            with self.assertRaises(AttributeError):
                self.run_cli("ingest", "--query", "sleep", db=False)
            with self.assertRaises(AttributeError):
                self.run_cli("search", "sleep", db=False)

        ingest.assert_called_once_with(
            "sleep", mode="title_abstract", max_records=100,
            year_from=None, year_to=None, database_path=None,
        )
        search.assert_called_once_with(
            "sleep", strategy="keyword", page=1, page_size=20, year_from=None,
            year_to=None, abstract_available=None, database_path=None,
        )

    def test_db_flag_is_accepted_before_or_after_the_command(self) -> None:
        with mock.patch.object(services, "get_stats") as get_stats:
            get_stats.side_effect = services.PaperStorageError("stop")
            for argv in (["--db", "x.sqlite3", "stats"], ["stats", "--db", "x.sqlite3"]):
                with redirect_stderr(StringIO()):
                    cli.main(argv)
        self.assertEqual(
            get_stats.call_args_list,
            [mock.call(database_path="x.sqlite3")] * 2,
        )

    def test_argparse_rejects_bad_usage_with_exit_code_two(self) -> None:
        bad_usages = (
            [],
            ["ingest"],
            ["ingest", "--query", "x", "--mode", "fulltext"],
            ["ingest", "--query", "x", "--max", "many"],
            ["search"],
            ["search", "x", "--type", "semantic"],
            ["show"],
            ["lists"],
            ["lists", "export", "Reading", "--format", "pdf"],
        )
        for argv in bad_usages:
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as raised, \
                        redirect_stderr(StringIO()):
                    cli.main(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_cli_module_only_depends_on_the_service_layer(self) -> None:
        imports = [
            line.strip()
            for line in Path(cli.__file__).read_text().splitlines()
            if line.startswith(("import ", "from "))
        ]
        self.assertIn("from . import services", imports)
        for line in imports:
            self.assertNotIn("ingestion", line)
            self.assertNotIn("sqlite3", line)


class FullTextCommandTests(CliTestCase):
    def fake_client(self):
        from test_fulltext_services import FakePmcClient

        return FakePmcClient()

    def run_fulltext(self, *argv: str, client=None):
        client = client or self.fake_client()
        with mock.patch(
            "hri.fulltext_services.build_pmc_client", return_value=client
        ), mock.patch.dict("os.environ", {"HRI_ALLOWED_LICENCES": ""}):
            return self.run_cli(*argv), client

    def test_fetch_fulltext_by_query_reports_counts(self) -> None:
        from test_fulltext_services import CC0, metadata

        client = self.fake_client()
        client.metadata[f"{CC0}.1"] = metadata(f"{CC0}.1", licence="CC BY-NC")

        (code, out, _), _ = self.run_fulltext(
            "fetch-fulltext", "--query", "adhd", "--max", "5", client=client
        )

        self.assertEqual(code, 0)
        for expected in (
            'Fetched open-access full text for "adhd"',
            "Candidates:", "Stored:", "Rejected (licence):",
            f"rejected {CC0}: licence (CC BY-NC)",
            "keeps its licence and attribution",
        ):
            self.assertIn(expected, out)
        self.assertRegex(out, r"Stored:\s+1\n")
        self.assertRegex(out, r"Rejected \(licence\):\s+1\n")
        self.assertNotIn("Papers not in PMC", out)

    def test_fetch_fulltext_from_db_and_stats(self) -> None:
        self.ingest([_record("35275515")])

        (code, out, _), _ = self.run_fulltext("fetch-fulltext", "--from-db")

        self.assertEqual(code, 0)
        self.assertIn("for stored papers", out)
        self.assertRegex(out, r"Stored:\s+1\n")
        self.assertRegex(out, r"Papers not in PMC:\s+0\n")
        _, stats, _ = self.run_cli("stats")
        self.assertIn("Full-text articles:   1", stats)

    def test_fetch_fulltext_requires_exactly_one_source(self) -> None:
        for argv in (
            ["fetch-fulltext"],
            ["fetch-fulltext", "--from-db", "--query", "x"],
            ["fetch-fulltext", "--query", "x", "--max", "many"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as raised, \
                        redirect_stderr(StringIO()):
                    cli.main(argv)
                self.assertEqual(raised.exception.code, 2)
        (code, _, err), _ = self.run_fulltext(
            "fetch-fulltext", "--query", "x", "--max", "500"
        )
        self.assertEqual(code, 2)
        self.assertIn("max_articles", err)

    def test_reports_a_bad_licence_setting(self) -> None:
        with mock.patch.dict("os.environ", {"HRI_ALLOWED_LICENCES": " , "}):
            code, _, err = self.run_cli("fetch-fulltext", "--query", "x")

        self.assertEqual(code, 1)
        self.assertIn("HRI_ALLOWED_LICENCES", err)

    def test_recheck_fulltext_reports_removals(self) -> None:
        from test_fulltext_services import CC_BY, metadata

        client = self.fake_client()
        self.run_fulltext("fetch-fulltext", "--query", "x", client=client)
        client.metadata[f"{CC_BY}.1"] = metadata(f"{CC_BY}.1", is_retracted=True)

        with self.assertLogs("hri.fulltext_services", "WARNING"):
            (code, out, _), _ = self.run_fulltext("recheck-fulltext", client=client)

        self.assertEqual(code, 0)
        self.assertIn("Re-checked 2 stored full-text articles", out)
        self.assertIn(f"removed: {CC_BY}", out)

    def test_search_passages_prints_text_with_attribution(self) -> None:
        self.run_fulltext("fetch-fulltext", "--query", "x")

        code, out, _ = self.run_cli(
            "search-passages", "veterans", "--section", "results", "--limit", "1"
        )

        self.assertEqual(code, 0)
        self.assertRegex(out, r"^1\. \[results\] .+ \(PMC12826829\.1, score ")
        self.assertIn("Licence: CC0", out)
        self.assertNotIn("2. [", out)
        code, out, _ = self.run_cli("search-passages", "zzzz")
        self.assertEqual((code, out), (0, "No stored passages match.\n"))
        code, _, err = self.run_cli("search-passages", "***")
        self.assertEqual(code, 2)
        self.assertIn("at least one word", err)


class EndToEndTests(CliTestCase):
    def test_ingest_search_show_and_stats(self) -> None:
        records = [
            _record("1", title="Sleep and memory"),
            _record("2", title="Sleep in adults", doi="10.1234/two"),
            _record("3", title="Diet and exercise"),
        ]

        code, out, _ = self.ingest(records, "--max", "50", "--year-from", "2018")
        self.assertEqual(code, 0)
        self.assertIn("Saved:             3", out)
        self.assertEqual(self.client.calls[0][2]["publication_year"], 2018)

        code, out, _ = self.run_cli("search", "sleep AND NOT adults", "--type", "boolean")
        self.assertEqual(code, 0)
        self.assertIn('1 matches for "sleep AND NOT adults" (boolean); page 1 of 1', out)
        self.assertIn("1. Sleep and memory (2021)", out)
        self.assertIn("https://pubmed.ncbi.nlm.nih.gov/1/", out)
        self.assertIn(PUBMED_ATTRIBUTION.independent_service_notice, out)
        self.assertNotIn("Sleep in adults", out)

        code, out, _ = self.run_cli("show", "10.1234/two")
        self.assertEqual(code, 0)
        for expected in (
            "Title:     Sleep in adults", "Authors:   Ada Lovelace; Grace Hopper",
            "PMID:      2", "DOI:       10.1234/two", "Source:    PubMed",
            "Link:      https://pubmed.ncbi.nlm.nih.gov/2/",
            PUBMED_ATTRIBUTION.source_name,
        ):
            self.assertIn(expected, out)

        code, out, _ = self.run_cli("stats")
        self.assertEqual(code, 0)
        self.assertIn("Total papers:         3", out)
        self.assertIn("With DOI:             1", out)
        self.assertIn("  2021: 1", out)

    def test_search_second_page_numbers_results_from_their_position(self) -> None:
        self.ingest([_record(str(number), title=f"Sleep {number}") for number in range(1, 6)])

        _, out, _ = self.run_cli("search", "sleep", "--page", "2", "--page-size", "2")

        self.assertIn("5 matches", out)
        self.assertIn("page 2 of 3", out)
        self.assertIn("3. Sleep 3", out)
        self.assertNotIn("1. Sleep 1", out)

    def test_saved_list_commands(self) -> None:
        self.ingest([_record("1", title="Sleep and memory"), _record("2")])

        self.assertIn("No saved lists.", self.run_cli("lists", "list")[1])
        self.assertIn('Created list "Reading".', self.run_cli("lists", "create", "Reading")[1])
        self.assertIn('Added to "Reading": Sleep and memory', self.run_cli("lists", "add", "Reading", "1")[1])
        self.assertIn("Already in", self.run_cli("lists", "add", "Reading", "1")[1])
        self.run_cli("lists", "add", "Reading", "2")
        self.assertIn("Reading (2 papers)", self.run_cli("lists", "list")[1])

        _, out, _ = self.run_cli("lists", "show", "Reading")
        self.assertIn('"Reading": 2 papers', out)
        self.assertIn("1. Sleep and memory", out)
        self.assertIn(PUBMED_ATTRIBUTION.source_name, out)

        _, out, _ = self.run_cli("lists", "remove", "Reading", "2")
        self.assertIn('Removed from "Reading"', out)
        self.assertIn("The list now has 1 papers.", out)
        self.assertIn("did not contain", self.run_cli("lists", "remove", "Reading", "2")[1])

    def test_rename_and_delete_list_commands(self) -> None:
        self.ingest([_record("1")])
        self.run_cli("lists", "create", "Reading")
        self.run_cli("lists", "add", "Reading", "1")

        code, out, _ = self.run_cli("lists", "rename", "Reading", "To read")
        self.assertEqual(code, 0)
        self.assertIn('Renamed list to "To read" (1 papers).', out)
        self.assertIn("To read (1 papers)", self.run_cli("lists", "list")[1])

        code, out, _ = self.run_cli("lists", "delete", "To read")
        self.assertEqual(code, 0)
        self.assertIn('Deleted list "To read" (1 papers).', out)
        self.assertIn("still stored", out)
        self.assertIn("No saved lists.", self.run_cli("lists", "list")[1])
        self.assertEqual(self.run_cli("show", "1")[0], 0)

        code, _, err = self.run_cli("lists", "delete", "To read")
        self.assertEqual(code, 1)
        self.assertIn("No saved list", err)
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            cli.main(["lists", "rename", "OnlyOneName"])

    def test_exports_to_the_screen_and_to_a_file(self) -> None:
        self.ingest([_record("1", title="Sleep and memory")])
        self.run_cli("lists", "create", "Reading")
        self.run_cli("lists", "add", "Reading", "1")

        _, out, _ = self.run_cli("lists", "export", "Reading")
        rows = list(csv.DictReader(StringIO(out)))
        self.assertEqual([row["pmid"] for row in rows], ["1"])

        _, out, _ = self.run_cli("lists", "export", "Reading", "--format", "citations")
        self.assertTrue(out.startswith("1. Ada Lovelace, Grace Hopper. Sleep and memory."))
        self.assertIn("PMID: 1.", out)
        self.assertIn(PUBMED_ATTRIBUTION.source_name, out)

        _, out, _ = self.run_cli("lists", "export", "Reading", "--format", "plain")
        self.assertIn("https://pubmed.ncbi.nlm.nih.gov/1/", out.splitlines()[0])

        target = Path(self.temp_dir.name) / "reading.csv"
        code, out, _ = self.run_cli(
            "lists", "export", "Reading", "--format", "csv", "--output", str(target)
        )
        self.assertEqual(code, 0)
        self.assertIn("Wrote 1 papers", out)
        self.assertIn(PUBMED_ATTRIBUTION.source_name, out)
        self.assertEqual(target.read_text(encoding="utf-8").splitlines()[0].split(",")[0], "pmid")

    def test_reports_errors_on_stderr_with_exit_codes(self) -> None:
        cases = (
            (("show", "999"), 1, "No stored paper"),
            (("show", "not an id"), 2, "DOI"),
            (("search", "sleep AND", "--type", "boolean"), 2, "Expected a term"),
            (("search", "sleep", "--page", "0"), 2, "page"),
            (("lists", "show", "Missing"), 1, "No saved list"),
            (("lists", "add", "Missing", "1"), 1, "No saved list"),
        )
        for argv, expected_code, expected_text in cases:
            with self.subTest(argv=argv):
                code, out, err = self.run_cli(*argv)
                self.assertEqual((code, out), (expected_code, ""))
                self.assertIn("hri: error:", err)
                self.assertIn(expected_text, err)

        self.run_cli("lists", "create", "Reading")
        code, _, err = self.run_cli("lists", "create", "reading")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_reports_pubmed_and_configuration_failures(self) -> None:
        failing = FakePubMedClient([], error=PubMedTransportError("Unable to reach the PubMed API."))
        with mock.patch.object(services, "build_pubmed_client", return_value=failing):
            code, _, err = self.run_cli("ingest", "--query", "sleep")
        self.assertEqual(code, 1)
        self.assertIn("Unable to reach the PubMed API.", err)

    def test_missing_pubmed_settings_fail_before_a_database_is_created(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            code, _, err = self.run_cli("ingest", "--query", "sleep")
        self.assertEqual(code, 1)
        self.assertIn("PUBMED_NCBI_TOOL", err)
        self.assertFalse(Path(self.db).exists())


if __name__ == "__main__":
    unittest.main()
