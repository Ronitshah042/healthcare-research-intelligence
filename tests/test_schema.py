"""Tests for creating and upgrading the local database layout."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ingestion.schema import SCHEMA_VERSION, SchemaVersionError, ensure_schema
from ingestion.storage import PaperStorageError, PaperStore


_PAPERS_BEFORE_SOURCE_NAME = """CREATE TABLE papers (
    id INTEGER PRIMARY KEY, pmid TEXT UNIQUE, doi TEXT UNIQUE,
    title TEXT NOT NULL, authors_json TEXT NOT NULL, journal TEXT,
    publication_year INTEGER, abstract_available INTEGER NOT NULL,
    source_identifier TEXT, source_url TEXT,
    CHECK (pmid IS NOT NULL OR doi IS NOT NULL)
)"""
_PAPERS_WITH_SOURCE_NAME = _PAPERS_BEFORE_SOURCE_NAME.replace(
    "source_url TEXT,", "source_url TEXT, source_name TEXT,"
)


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "papers.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _execute(self, *statements: str) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for statement in statements:
                connection.execute(statement)

    def _tables_and_version(self) -> tuple[set[str], int]:
        with closing(sqlite3.connect(self.path)) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            return tables, connection.execute("PRAGMA user_version").fetchone()[0]

    def test_new_database_gets_every_table_and_the_current_version(self) -> None:
        PaperStore(self.path)

        tables, version = self._tables_and_version()

        self.assertLessEqual(
            {"papers", "saved_lists", "saved_list_items", "documents",
             "document_sections", "chunks", "fulltext_rejections"},
            tables,
        )
        self.assertEqual(version, SCHEMA_VERSION)

    def test_upgrades_every_earlier_layout_without_losing_rows(self) -> None:
        layouts = {
            "before source_name": ((_PAPERS_BEFORE_SOURCE_NAME,), "PubMed"),
            "with source_name, no version": ((_PAPERS_WITH_SOURCE_NAME,), None),
            "version 2, before citation details": (
                (
                    _PAPERS_WITH_SOURCE_NAME,
                    "CREATE TABLE saved_lists (id INTEGER PRIMARY KEY, "
                    "name TEXT NOT NULL UNIQUE COLLATE NOCASE)",
                    "CREATE TABLE saved_list_items (id INTEGER PRIMARY KEY, "
                    "list_id INTEGER NOT NULL, paper_id INTEGER NOT NULL, "
                    "UNIQUE (list_id, paper_id))",
                    "INSERT INTO saved_lists (name) VALUES ('Kept list')",
                    "PRAGMA user_version = 2",
                ),
                None,
            ),
        }
        for name, (statements, expected_source) in layouts.items():
            with self.subTest(layout=name):
                if self.path.exists():
                    self.path.unlink()
                self._execute(
                    *statements,
                    """INSERT INTO papers (pmid, title, authors_json, abstract_available)
                    VALUES ('42', 'Older paper', '["Ada Lovelace"]', 1)""",
                )

                paper = PaperStore(self.path).get(pmid="42")

                tables, version = self._tables_and_version()
                self.assertEqual(paper.title, "Older paper")
                self.assertEqual(paper.authors, ("Ada Lovelace",))
                self.assertEqual(paper.source_name, expected_source)
                self.assertIsNone(paper.citation)
                self.assertIn("saved_lists", tables)
                self.assertEqual(version, SCHEMA_VERSION)

    def test_upgrades_version_3_by_adding_the_full_text_tables(self) -> None:
        PaperStore(self.path)  # build the current layout, then roll it back
        self._execute(
            "DROP TABLE documents", "DROP TABLE document_sections",
            "DROP TABLE chunks", "DROP TABLE fulltext_rejections",
            """INSERT INTO papers (pmid, title, authors_json, abstract_available)
            VALUES ('42', 'Older paper', '[]', 1)""",
            "INSERT INTO saved_lists (name) VALUES ('Kept')",
            "PRAGMA user_version = 3",
        )

        store = PaperStore(self.path)

        tables, version = self._tables_and_version()
        self.assertLessEqual(
            {"documents", "document_sections", "chunks", "fulltext_rejections"},
            tables,
        )
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(store.get(pmid="42").title, "Older paper")
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT name FROM saved_lists").fetchall(),
                [("Kept",)],
            )

    def test_upgrades_version_4_by_indexing_stored_passages(self) -> None:
        from ingestion.chunking import chunk_sections
        from ingestion.fulltext import ArticleSection
        from test_fulltext_store import DOCUMENT
        from ingestion.fulltext_store import FullTextStore

        section = (ArticleSection("results", None, ("Mortality fell sharply.",)),)
        FullTextStore(self.path).save(DOCUMENT, section, chunk_sections(section))
        self._execute(
            "DROP TRIGGER chunks_fts_insert", "DROP TRIGGER chunks_fts_delete",
            "DROP TRIGGER chunks_fts_update", "DROP TABLE chunks_fts",
            "PRAGMA user_version = 4",
        )

        store = FullTextStore(self.path)

        tables, version = self._tables_and_version()
        self.assertIn("chunks_fts", tables)
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(
            [m.chunk.text for m in store.search("mortality", limit=5)],
            ["Mortality fell sharply."],
        )
        store.reject("PMC10009402", "retracted", recorded_at="2026-11-18T00:00:00Z")
        self.assertEqual(store.search("mortality", limit=5), ())

    def test_opening_an_up_to_date_database_changes_nothing(self) -> None:
        PaperStore(self.path)
        self._execute(
            """INSERT INTO papers (pmid, title, authors_json, abstract_available)
            VALUES ('1', 'Kept', '[]', 0)""",
            "INSERT INTO saved_lists (name) VALUES ('Reading')",
        )

        store = PaperStore(self.path)

        self.assertEqual(store.get(pmid="1").title, "Kept")
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT name FROM saved_lists").fetchall(),
                [("Reading",)],
            )

    def test_refuses_a_database_from_a_newer_version(self) -> None:
        PaperStore(self.path)
        self._execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

        with self.assertRaises(PaperStorageError) as raised:
            PaperStore(self.path)
        self.assertIn("newer version", str(raised.exception))
        with closing(sqlite3.connect(self.path)) as connection:
            with self.assertRaises(SchemaVersionError):
                ensure_schema(connection)


if __name__ == "__main__":
    unittest.main()
