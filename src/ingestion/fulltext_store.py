"""SQLite storage for licensed open-access full text and rejections.

The store enforces one rule from docs/CONTENT_POLICY.md itself: an article
recorded as rejected has no stored text, and a stored article has no
rejection record.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .chunking import Chunk
from .fulltext import ArticleSection
from .identifiers import strip_pmid_leading_zeros
from .schema import REJECTION_REASONS
from .storage import PaperStorageError, PaperStore, StoredPaper, _normalize_doi


@dataclass(frozen=True)
class FullTextDocument:
    """One stored open-access article version and its provenance."""

    pmcid: str
    # The exact version stored, such as ``PMC10009402.1``.
    pmcid_version: str
    pmid: str | None
    doi: str | None
    title: str
    # ``license_code`` exactly as received from PMC.
    licence: str
    source_url: str
    # UTC, ISO 8601, for example ``2026-11-17T09:30:00Z``.
    retrieved_at: str
    attribution: str
    article_type: str | None
    notice_flags: tuple[str, ...]


@dataclass(frozen=True)
class StoredChunk:
    """One passage of a stored article, with what is needed to cite it."""

    pmcid: str
    pmcid_version: str
    position: int
    section_index: int
    section_name: str
    text: str
    token_count: int
    licence: str


@dataclass(frozen=True)
class PassageMatch:
    """One passage found by keyword search, with its document for citing it."""

    chunk: StoredChunk
    document: FullTextDocument
    # BM25 relevance; higher is better. Only comparable within one search.
    score: float


@dataclass(frozen=True)
class FullTextRejection:
    """An article that was not stored, and why. Never holds text."""

    pmcid: str
    pmcid_version: str | None
    licence: str | None
    reason: str
    recorded_at: str


@dataclass(frozen=True)
class FullTextStats:
    """Counts describing stored full text and rejections."""

    documents: int
    chunks: int
    flagged_documents: int
    rejections_by_reason: tuple[tuple[str, int], ...]


class FullTextStore:
    """Save, read, and reject open-access articles in the paper database."""

    def __init__(self, database_path: str | Path, *, read_only: bool | None = None) -> None:
        # PaperStore creates or upgrades the database, including these tables;
        # ``read_only`` (default: HRI_DB_READ_ONLY) opens it without writing.
        self._papers = PaperStore(database_path, read_only=read_only)

    def save(
        self,
        document: FullTextDocument,
        sections: tuple[ArticleSection, ...],
        chunks: tuple[Chunk, ...],
    ) -> FullTextDocument:
        """Store an article version, replacing any earlier one for its PMCID."""
        document = _normalise_document(document)
        try:
            with closing(self._papers._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                _delete_document(connection, document.pmcid)
                connection.execute(
                    "DELETE FROM fulltext_rejections WHERE pmcid = ?",
                    (document.pmcid,),
                )
                cursor = connection.execute(
                    """INSERT INTO documents (
                        pmcid, pmcid_version, pmid, doi, title, licence,
                        source_url, retrieved_at, attribution, article_type,
                        notice_flags_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        document.pmcid, document.pmcid_version, document.pmid,
                        document.doi, document.title, document.licence,
                        document.source_url, document.retrieved_at,
                        document.attribution, document.article_type,
                        json.dumps(list(document.notice_flags)),
                    ),
                )
                document_id = cursor.lastrowid
                connection.executemany(
                    """INSERT INTO document_sections
                        (document_id, section_index, name, heading, text)
                        VALUES (?, ?, ?, ?, ?)""",
                    [
                        (document_id, index, section.name, section.heading,
                         "\n".join(section.paragraphs))
                        for index, section in enumerate(sections)
                    ],
                )
                connection.executemany(
                    """INSERT INTO chunks (
                        document_id, position, section_index, section_name,
                        text, token_count, licence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (document_id, chunk.position, chunk.section_index,
                         chunk.section_name, chunk.text, chunk.token_count,
                         document.licence)
                        for chunk in chunks
                    ],
                )
        except sqlite3.Error as error:
            raise PaperStorageError("Could not save full-text document.") from error
        return document

    def reject(
        self,
        pmcid: str,
        reason: str,
        *,
        recorded_at: str,
        pmcid_version: str | None = None,
        licence: str | None = None,
    ) -> FullTextRejection:
        """Record a rejection and delete any text stored for that PMCID."""
        if reason not in REJECTION_REASONS:
            raise ValueError(f"Unknown rejection reason: {reason!r}.")
        pmcid = _normalise_pmcid(pmcid)
        try:
            with closing(self._papers._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                _delete_document(connection, pmcid)
                connection.execute(
                    """INSERT INTO fulltext_rejections
                        (pmcid, pmcid_version, licence, reason, recorded_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (pmcid) DO UPDATE SET
                            pmcid_version = excluded.pmcid_version,
                            licence = excluded.licence,
                            reason = excluded.reason,
                            recorded_at = excluded.recorded_at""",
                    (pmcid, pmcid_version, licence, reason, recorded_at),
                )
        except sqlite3.Error as error:
            raise PaperStorageError("Could not record full-text rejection.") from error
        return FullTextRejection(pmcid, pmcid_version, licence, reason, recorded_at)

    def set_notice_flags(self, pmcid: str, flags: tuple[str, ...]) -> None:
        """Replace a stored document's notice flags; its text is unchanged."""
        try:
            with closing(self._papers._connect()) as connection, connection:
                connection.execute(
                    "UPDATE documents SET notice_flags_json = ? WHERE pmcid = ?",
                    (json.dumps(list(flags)), _normalise_pmcid(pmcid)),
                )
        except sqlite3.Error as error:
            raise PaperStorageError("Could not update notice flags.") from error

    def get(self, pmcid: str) -> FullTextDocument | None:
        """Return the stored document for a PMCID, or ``None``."""
        rows = self._select(
            "SELECT * FROM documents WHERE pmcid = ?", (_normalise_pmcid(pmcid),)
        )
        return _to_document(rows[0]) if rows else None

    def find(
        self, *, pmid: str | None = None, doi: str | None = None
    ) -> FullTextDocument | None:
        """Return the stored document linked to a PMID or DOI, or ``None``."""
        if (pmid is None) == (doi is None):
            raise ValueError("Provide exactly one of pmid or doi.")
        if pmid is not None:
            rows = self._select(
                "SELECT * FROM documents WHERE pmid = ?",
                (strip_pmid_leading_zeros(pmid.strip()),),
            )
        else:
            rows = self._select(
                "SELECT * FROM documents WHERE doi = ?", (_normalize_doi(doi),)
            )
        return _to_document(rows[0]) if rows else None

    def documents(self) -> tuple[FullTextDocument, ...]:
        """Return every stored document, oldest first."""
        return tuple(
            _to_document(row)
            for row in self._select("SELECT * FROM documents ORDER BY id", ())
        )

    def sections(self, pmcid: str) -> tuple[ArticleSection, ...]:
        """Return a stored document's sections in reading order."""
        rows = self._select(
            """SELECT document_sections.* FROM document_sections
                JOIN documents ON documents.id = document_sections.document_id
                WHERE documents.pmcid = ? ORDER BY section_index""",
            (_normalise_pmcid(pmcid),),
        )
        return tuple(
            ArticleSection(row["name"], row["heading"], tuple(row["text"].split("\n")))
            for row in rows
        )

    def chunks(self, pmcid: str) -> tuple[StoredChunk, ...]:
        """Return a stored document's chunks in order."""
        rows = self._select(
            """SELECT chunks.*, documents.pmcid, documents.pmcid_version
                FROM chunks JOIN documents ON documents.id = chunks.document_id
                WHERE documents.pmcid = ? ORDER BY position""",
            (_normalise_pmcid(pmcid),),
        )
        return tuple(
            StoredChunk(
                pmcid=row["pmcid"], pmcid_version=row["pmcid_version"],
                position=row["position"], section_index=row["section_index"],
                section_name=row["section_name"], text=row["text"],
                token_count=row["token_count"], licence=row["licence"],
            )
            for row in rows
        )

    def search(
        self,
        query: str,
        *,
        limit: int,
        sections: tuple[str, ...] = (),
        match_all: bool = True,
    ) -> tuple[PassageMatch, ...]:
        """Return the passages containing every word of ``query``, best first.

        With ``match_all=False``, passages containing any of the words match;
        BM25 still ranks those with more, and rarer, words first.

        Words are matched after Porter stemming, ignoring case and accents.
        Each word is quoted, so FTS5 operators and syntax in ``query`` are
        searched for as plain text and cannot change the search.
        """
        words = tuple(dict.fromkeys(re.findall(r"\w+", query.casefold())))
        if not words:
            raise ValueError("Search text must contain at least one word.")
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        section_filter = ""
        if sections:
            section_filter = (
                f" AND chunks.section_name IN ({', '.join('?' * len(sections))})"
            )
        rows = self._select(
            f"""SELECT {_PASSAGE_COLUMNS}, bm25(chunks_fts) AS rank
                FROM chunks_fts
                JOIN chunks ON chunks.id = chunks_fts.rowid
                JOIN documents ON documents.id = chunks.document_id
                WHERE chunks_fts MATCH ?{section_filter}
                ORDER BY rank, chunks.id LIMIT ?""",
            (
                (" " if match_all else " OR ").join(f'"{word}"' for word in words),
                *sections, limit,
            ),
        )
        # SQLite's bm25() is negative, lower meaning more relevant.
        return tuple(_to_match(row, -row["rank"]) for row in rows)

    def passages(self, chunk_ids: Iterable[int]) -> dict[int, PassageMatch]:
        """Return stored passages by chunk ID, with a score of 0."""
        ids = list(dict.fromkeys(chunk_ids))
        found: dict[int, PassageMatch] = {}
        # Stay well under SQLite's limit on query parameters.
        for start in range(0, len(ids), 500):
            batch = ids[start : start + 500]
            rows = self._select(
                f"""SELECT {_PASSAGE_COLUMNS} FROM chunks
                    JOIN documents ON documents.id = chunks.document_id
                    WHERE chunks.id IN ({', '.join('?' * len(batch))})""",
                tuple(batch),
            )
            found.update((row["chunk_id"], _to_match(row, 0.0)) for row in rows)
        return found

    def embeddings(
        self, model: str, *, sections: tuple[str, ...] = ()
    ) -> list[tuple[int, bytes]]:
        """Return ``(chunk_id, vector)`` for every passage embedded by ``model``."""
        section_filter = ""
        if sections:
            section_filter = (
                f" AND chunks.section_name IN ({', '.join('?' * len(sections))})"
            )
        rows = self._select(
            f"""SELECT chunk_embeddings.chunk_id, chunk_embeddings.vector
                FROM chunk_embeddings
                JOIN chunks ON chunks.id = chunk_embeddings.chunk_id
                WHERE chunk_embeddings.model = ?{section_filter}""",
            (model, *sections),
        )
        return [(row["chunk_id"], row["vector"]) for row in rows]

    def chunks_to_embed(self, model: str, *, limit: int) -> list[tuple[int, str]]:
        """Return up to ``limit`` ``(chunk_id, text)`` pairs with no vector yet."""
        rows = self._select(
            """SELECT chunks.id, chunks.text FROM chunks
                WHERE NOT EXISTS (
                    SELECT 1 FROM chunk_embeddings
                    WHERE chunk_id = chunks.id AND model = ?
                )
                ORDER BY chunks.id LIMIT ?""",
            (model, limit),
        )
        return [(row["id"], row["text"]) for row in rows]

    def count_unembedded(self, model: str) -> int:
        """Count stored passages that have no vector from ``model``."""
        rows = self._select(
            """SELECT COUNT(*) FROM chunks WHERE NOT EXISTS (
                SELECT 1 FROM chunk_embeddings
                WHERE chunk_id = chunks.id AND model = ?
            )""",
            (model,),
        )
        return rows[0][0]

    def save_embeddings(
        self, model: str, vectors: Iterable[tuple[int, bytes]]
    ) -> None:
        """Store vectors for passages; a passage deleted meanwhile is skipped."""
        try:
            with closing(self._papers._connect()) as connection, connection:
                connection.executemany(
                    """INSERT OR REPLACE INTO chunk_embeddings (chunk_id, model, vector)
                        SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM chunks WHERE id = ?)""",
                    [(chunk_id, model, vector, chunk_id) for chunk_id, vector in vectors],
                )
        except sqlite3.Error as error:
            raise PaperStorageError("Could not save passage embeddings.") from error

    def rejections(self) -> tuple[FullTextRejection, ...]:
        """Return every recorded rejection, most recent first."""
        rows = self._select(
            "SELECT * FROM fulltext_rejections ORDER BY recorded_at DESC, id DESC", ()
        )
        return tuple(
            FullTextRejection(
                row["pmcid"], row["pmcid_version"], row["licence"],
                row["reason"], row["recorded_at"],
            )
            for row in rows
        )

    def stats(self) -> FullTextStats:
        """Count stored documents, chunks, flagged documents, and rejections."""
        try:
            with closing(self._papers._connect()) as connection, connection:
                documents, flagged = connection.execute(
                    """SELECT COUNT(*), COALESCE(SUM(notice_flags_json <> '[]'), 0)
                        FROM documents"""
                ).fetchone()
                chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                reasons = connection.execute(
                    """SELECT reason, COUNT(*) FROM fulltext_rejections
                        GROUP BY reason ORDER BY reason"""
                ).fetchall()
        except sqlite3.Error as error:
            raise PaperStorageError("Could not count full text.") from error
        return FullTextStats(
            documents=documents,
            chunks=chunks,
            flagged_documents=flagged,
            rejections_by_reason=tuple((reason, count) for reason, count in reasons),
        )

    def linked_paper(self, document: FullTextDocument) -> StoredPaper | None:
        """Return the stored PubMed paper with the same PMID, else the same DOI."""
        if document.pmid:
            paper = self._papers.get(pmid=document.pmid)
            if paper is not None:
                return paper
        if document.doi:
            return self._papers.get(doi=document.doi)
        return None

    def _select(self, sql: str, parameters: tuple[object, ...]) -> list[sqlite3.Row]:
        try:
            with closing(self._papers._connect()) as connection, connection:
                return connection.execute(sql, parameters).fetchall()
        except sqlite3.Error as error:
            raise PaperStorageError("Could not read full text.") from error


_PASSAGE_COLUMNS = """documents.*, chunks.id AS chunk_id, chunks.position,
    chunks.section_index, chunks.section_name, chunks.text AS chunk_text,
    chunks.token_count, chunks.licence AS chunk_licence"""


def _to_match(row: sqlite3.Row, score: float) -> PassageMatch:
    return PassageMatch(
        chunk=StoredChunk(
            pmcid=row["pmcid"], pmcid_version=row["pmcid_version"],
            position=row["position"], section_index=row["section_index"],
            section_name=row["section_name"], text=row["chunk_text"],
            token_count=row["token_count"], licence=row["chunk_licence"],
        ),
        document=_to_document(row),
        score=score,
    )


def _delete_document(connection: sqlite3.Connection, pmcid: str) -> None:
    row = connection.execute(
        "SELECT id FROM documents WHERE pmcid = ?", (pmcid,)
    ).fetchone()
    if row is None:
        return
    connection.execute("DELETE FROM chunks WHERE document_id = ?", (row[0],))
    connection.execute("DELETE FROM document_sections WHERE document_id = ?", (row[0],))
    connection.execute("DELETE FROM documents WHERE id = ?", (row[0],))


def _normalise_pmcid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("PMCID must be text.")
    pmcid = value.strip().upper()
    if not pmcid.startswith("PMC"):
        pmcid = "PMC" + pmcid
    number = pmcid[3:]
    if not (number.isascii() and number.isdigit()):
        raise ValueError("PMCID must be PMC followed by digits.")
    return "PMC" + strip_pmid_leading_zeros(number)


def _normalise_document(document: FullTextDocument) -> FullTextDocument:
    pmcid = _normalise_pmcid(document.pmcid)
    if not document.pmcid_version.startswith(pmcid + "."):
        raise ValueError("pmcid_version must be the PMCID followed by a version.")
    if not document.licence.strip() or not document.attribution.strip():
        raise ValueError("A stored document needs its licence and attribution.")
    return FullTextDocument(
        pmcid=pmcid,
        pmcid_version=document.pmcid_version,
        pmid=strip_pmid_leading_zeros(document.pmid.strip()) if document.pmid else None,
        doi=_normalize_doi(document.doi) if document.doi else None,
        title=document.title,
        licence=document.licence,
        source_url=document.source_url,
        retrieved_at=document.retrieved_at,
        attribution=document.attribution,
        article_type=document.article_type,
        notice_flags=tuple(document.notice_flags),
    )


def _to_document(row: sqlite3.Row) -> FullTextDocument:
    return FullTextDocument(
        pmcid=row["pmcid"],
        pmcid_version=row["pmcid_version"],
        pmid=row["pmid"],
        doi=row["doi"],
        title=row["title"],
        licence=row["licence"],
        source_url=row["source_url"],
        retrieved_at=row["retrieved_at"],
        attribution=row["attribution"],
        article_type=row["article_type"],
        notice_flags=tuple(json.loads(row["notice_flags_json"])),
    )
