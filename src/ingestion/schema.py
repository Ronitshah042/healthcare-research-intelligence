"""Creation and step-by-step upgrades of the local SQLite database layout."""

from __future__ import annotations

import sqlite3

from .pubmed import PUBMED_SOURCE_NAME


# Version 1: the ``papers`` table, including ``source_name``.
# Version 2: saved lists (``saved_lists`` and ``saved_list_items``).
# Version 3: ``papers.citation_json`` for formal citation details.
# Version 4: open-access full text (``documents``, ``document_sections``,
#            ``chunks``) and ``fulltext_rejections``.
SCHEMA_VERSION = 4
REJECTION_REASONS = (
    "licence", "retracted", "not_open_access", "current_version_unavailable",
)


class SchemaVersionError(RuntimeError):
    """Raised when a database was written by a newer version of this project."""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create or upgrade the database so it matches ``SCHEMA_VERSION``.

    Databases made before versions were recorded report version 0 and are
    upgraded in place without losing rows. Every step is safe to repeat.
    """
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise SchemaVersionError(
            "The database was created by a newer version of this project."
        )

    # One writer upgrades at a time; the version is re-read under the lock.
    connection.execute("BEGIN IMMEDIATE")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        _create_papers(connection)
    if version < 2:
        _create_saved_lists(connection)
    if version < 3 and "citation_json" not in _paper_columns(connection):
        connection.execute("ALTER TABLE papers ADD COLUMN citation_json TEXT")
    if version < 4:
        _create_full_text(connection)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _create_papers(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY,
            pmid TEXT UNIQUE,
            doi TEXT UNIQUE,
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            journal TEXT,
            publication_year INTEGER,
            abstract_available INTEGER NOT NULL,
            source_identifier TEXT,
            source_url TEXT,
            source_name TEXT,
            citation_json TEXT,
            CHECK (pmid IS NOT NULL OR doi IS NOT NULL)
        )"""
    )
    if "source_name" not in _paper_columns(connection):
        # Databases from before sources were recorded only ever held PubMed
        # citations, so mark their rows as PubMed.
        connection.execute("ALTER TABLE papers ADD COLUMN source_name TEXT")
        connection.execute(
            "UPDATE papers SET source_name = ?", (PUBMED_SOURCE_NAME,)
        )


def _paper_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(papers)")}


def _create_saved_lists(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS saved_lists (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS saved_list_items (
            id INTEGER PRIMARY KEY,
            list_id INTEGER NOT NULL REFERENCES saved_lists(id),
            paper_id INTEGER NOT NULL REFERENCES papers(id),
            UNIQUE (list_id, paper_id)
        )"""
    )


def _create_full_text(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            pmcid TEXT NOT NULL UNIQUE,
            pmcid_version TEXT NOT NULL,
            pmid TEXT,
            doi TEXT,
            title TEXT NOT NULL,
            licence TEXT NOT NULL,
            source_url TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            attribution TEXT NOT NULL,
            article_type TEXT,
            notice_flags_json TEXT NOT NULL
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS documents_pmid ON documents(pmid)")
    connection.execute("CREATE INDEX IF NOT EXISTS documents_doi ON documents(doi)")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS document_sections (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id),
            section_index INTEGER NOT NULL,
            name TEXT NOT NULL,
            heading TEXT,
            text TEXT NOT NULL,
            UNIQUE (document_id, section_index)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id),
            position INTEGER NOT NULL,
            section_index INTEGER NOT NULL,
            section_name TEXT NOT NULL,
            text TEXT NOT NULL,
            token_count INTEGER NOT NULL,
            licence TEXT NOT NULL,
            UNIQUE (document_id, position)
        )"""
    )
    reasons = ", ".join(f"'{reason}'" for reason in REJECTION_REASONS)
    connection.execute(
        f"""CREATE TABLE IF NOT EXISTS fulltext_rejections (
            id INTEGER PRIMARY KEY,
            pmcid TEXT NOT NULL UNIQUE,
            pmcid_version TEXT,
            licence TEXT,
            reason TEXT NOT NULL CHECK (reason IN ({reasons})),
            recorded_at TEXT NOT NULL
        )"""
    )
