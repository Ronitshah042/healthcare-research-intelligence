"""Named lists of stored papers, kept in the same SQLite database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .storage import (
    PaperIdentityConflictError,
    PaperStorageError,
    PaperStore,
    StoredPaper,
    _validate_lookup,
)

MAX_LIST_NAME_LENGTH = 100


class SavedListNotFoundError(PaperStorageError):
    """Raised when no saved list has the requested name."""


class SavedListExistsError(PaperStorageError):
    """Raised when a saved list with the same name already exists."""


class PaperNotFoundError(PaperStorageError):
    """Raised when a PMID or DOI does not match any stored paper."""


@dataclass(frozen=True)
class SavedList:
    """A saved list's name and how many papers it holds."""

    name: str
    paper_count: int


class SavedListStore:
    """Create saved lists and add, remove, or read their papers.

    List names are unique ignoring case. A list holds each paper at most once
    and keeps papers in the order they were added.
    """

    def __init__(self, database_path: str | Path) -> None:
        # PaperStore creates or upgrades the database, including list tables,
        # and this class reuses its connection and row helpers.
        self._papers = PaperStore(database_path)

    def create(self, name: str) -> SavedList:
        """Create an empty list, or raise ``SavedListExistsError``."""
        list_name = _normalise_list_name(name)
        try:
            with closing(self._papers._connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO saved_lists (name) VALUES (?)", (list_name,)
                )
        except sqlite3.IntegrityError as error:
            raise SavedListExistsError(
                f"A saved list named '{list_name}' already exists."
            ) from error
        except sqlite3.Error as error:
            raise PaperStorageError("Could not create saved list.") from error
        return SavedList(name=list_name, paper_count=0)

    def rename(self, name: str, new_name: str) -> SavedList:
        """Rename a list, keeping its papers; the new name must be unused."""
        new_list_name = _normalise_list_name(new_name)
        try:
            with closing(self._papers._connect()) as connection, connection:
                list_id, _ = _find_list(connection, name)
                connection.execute(
                    "UPDATE saved_lists SET name = ? WHERE id = ?",
                    (new_list_name, list_id),
                )
                return SavedList(new_list_name, _count_papers(connection, list_id))
        except PaperStorageError:
            raise
        except sqlite3.IntegrityError as error:
            raise SavedListExistsError(
                f"A saved list named '{new_list_name}' already exists."
            ) from error
        except sqlite3.Error as error:
            raise PaperStorageError("Could not rename saved list.") from error

    def delete(self, name: str) -> SavedList:
        """Delete a list and return what it was; its papers stay stored."""
        try:
            with closing(self._papers._connect()) as connection, connection:
                list_id, list_name = _find_list(connection, name)
                deleted = SavedList(list_name, _count_papers(connection, list_id))
                connection.execute(
                    "DELETE FROM saved_list_items WHERE list_id = ?", (list_id,)
                )
                connection.execute("DELETE FROM saved_lists WHERE id = ?", (list_id,))
                return deleted
        except PaperStorageError:
            raise
        except sqlite3.Error as error:
            raise PaperStorageError("Could not delete saved list.") from error

    def lists(self) -> tuple[SavedList, ...]:
        """Return every saved list in creation order."""
        try:
            with closing(self._papers._connect()) as connection, connection:
                rows = connection.execute(
                    """SELECT saved_lists.name, COUNT(saved_list_items.id)
                        FROM saved_lists LEFT JOIN saved_list_items
                            ON saved_list_items.list_id = saved_lists.id
                        GROUP BY saved_lists.id ORDER BY saved_lists.id"""
                ).fetchall()
        except sqlite3.Error as error:
            raise PaperStorageError("Could not read saved lists.") from error
        return tuple(SavedList(name=name, paper_count=count) for name, count in rows)

    def papers(self, name: str) -> tuple[StoredPaper, ...]:
        """Return a list's papers in the order they were added."""
        try:
            with closing(self._papers._connect()) as connection, connection:
                list_id, _ = _find_list(connection, name)
                rows = connection.execute(
                    """SELECT papers.* FROM saved_list_items
                        JOIN papers ON papers.id = saved_list_items.paper_id
                        WHERE saved_list_items.list_id = ?
                        ORDER BY saved_list_items.id""",
                    (list_id,),
                ).fetchall()
                return tuple(PaperStore._to_paper(row) for row in rows)
        except PaperStorageError:
            raise
        except sqlite3.Error as error:
            raise PaperStorageError("Could not read saved list.") from error

    def add(
        self, name: str, *, pmid: str | None = None, doi: str | None = None
    ) -> bool:
        """Add a stored paper; return ``False`` if the list already had it."""
        try:
            with closing(self._papers._connect()) as connection, connection:
                list_id, _ = _find_list(connection, name)
                paper_id = _find_paper_id(connection, pmid, doi)
                if paper_id is None:
                    raise PaperNotFoundError("No stored paper has that identifier.")
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO saved_list_items (list_id, paper_id)
                        VALUES (?, ?)""",
                    (list_id, paper_id),
                )
                return cursor.rowcount == 1
        except PaperStorageError:
            raise
        except sqlite3.Error as error:
            raise PaperStorageError("Could not add paper to saved list.") from error

    def remove(
        self, name: str, *, pmid: str | None = None, doi: str | None = None
    ) -> bool:
        """Remove a paper; return ``False`` if the list did not hold it."""
        try:
            with closing(self._papers._connect()) as connection, connection:
                list_id, _ = _find_list(connection, name)
                paper_id = _find_paper_id(connection, pmid, doi)
                cursor = connection.execute(
                    "DELETE FROM saved_list_items WHERE list_id = ? AND paper_id = ?",
                    (list_id, paper_id),
                )
                return cursor.rowcount == 1
        except PaperStorageError:
            raise
        except sqlite3.Error as error:
            raise PaperStorageError(
                "Could not remove paper from saved list."
            ) from error


def _normalise_list_name(name: object) -> str:
    if not isinstance(name, str):
        raise ValueError("List name must be text.")
    list_name = " ".join(name.split())
    if not list_name:
        raise ValueError("List name must not be empty.")
    if len(list_name) > MAX_LIST_NAME_LENGTH:
        raise ValueError(
            f"List name must be at most {MAX_LIST_NAME_LENGTH} characters."
        )
    return list_name


def _find_list(connection: sqlite3.Connection, name: object) -> tuple[int, str]:
    list_name = _normalise_list_name(name)
    row = connection.execute(
        "SELECT id, name FROM saved_lists WHERE name = ?", (list_name,)
    ).fetchone()
    if row is None:
        raise SavedListNotFoundError(f"No saved list is named '{list_name}'.")
    return row[0], row[1]


def _count_papers(connection: sqlite3.Connection, list_id: int) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM saved_list_items WHERE list_id = ?", (list_id,)
    ).fetchone()[0]


def _find_paper_id(
    connection: sqlite3.Connection, pmid: str | None, doi: str | None
) -> int | None:
    rows = PaperStore._matching_rows(connection, *_validate_lookup(pmid, doi))
    if len(rows) > 1:
        raise PaperIdentityConflictError("Identifier matches multiple stored papers.")
    return rows[0]["id"] if rows else None
