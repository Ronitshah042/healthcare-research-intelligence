"""SQLite-backed storage for standardised research-paper citations."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sqlite3

from .pipeline import StandardisedPaper, _strip_doi_prefix


_BOOLEAN_TOKEN_PATTERN = re.compile(
    r'\s*(?:(?P<operator>AND|OR|NOT)(?=[\s()"]|$)|(?P<parenthesis>[()])|'
    r'"(?P<phrase>[^"]*)"|(?P<term>[^\s()"]+))',
    re.IGNORECASE,
)


class PaperStorageError(RuntimeError):
    """Raised when paper data cannot be saved or read."""


class PaperIdentityConflictError(PaperStorageError):
    """Raised when identifiers point to different papers."""


@dataclass(frozen=True)
class StoredPaper:
    """A paper citation read from the local database."""

    pmid: str | None
    doi: str | None
    title: str
    authors: tuple[str, ...]
    journal: str | None
    publication_year: int | None
    abstract_available: bool
    source_identifier: str | None
    source_url: str | None


@dataclass(frozen=True)
class _BooleanToken:
    kind: str
    value: str


class _BooleanParser:
    """Parse Boolean search expressions using NOT > AND > OR precedence."""

    def __init__(self, tokens: tuple[_BooleanToken, ...]) -> None:
        self._tokens = tokens
        self._position = 0

    def parse(self) -> tuple[object, ...]:
        if not self._tokens:
            raise ValueError("Boolean search expression must not be empty.")
        expression = self._parse_or()
        if self._position != len(self._tokens):
            raise ValueError("Invalid Boolean search expression.")
        return expression

    def _parse_or(self) -> tuple[object, ...]:
        expression = self._parse_and()
        while self._matches("operator", "OR"):
            self._position += 1
            expression = ("or", expression, self._parse_and())
        return expression

    def _parse_and(self) -> tuple[object, ...]:
        expression = self._parse_unary()
        while self._matches("operator", "AND"):
            self._position += 1
            expression = ("and", expression, self._parse_unary())
        return expression

    def _parse_unary(self) -> tuple[object, ...]:
        if self._matches("operator", "NOT"):
            self._position += 1
            return ("not", self._parse_unary())
        if self._matches("parenthesis", "("):
            self._position += 1
            expression = self._parse_or()
            if not self._matches("parenthesis", ")"):
                raise ValueError("Unclosed parenthesis in Boolean search expression.")
            self._position += 1
            return expression
        token = self._peek()
        if token is not None and token.kind == "term":
            self._position += 1
            return ("term", token.value.casefold())
        raise ValueError("Expected a term or parenthesized expression.")

    def _matches(self, kind: str, value: str) -> bool:
        token = self._peek()
        return token is not None and token.kind == kind and token.value.upper() == value

    def _peek(self) -> _BooleanToken | None:
        if self._position >= len(self._tokens):
            return None
        return self._tokens[self._position]


class PaperStore:
    """Save and retrieve citation metadata in a local SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        try:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection, connection:
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
                        CHECK (pmid IS NOT NULL OR doi IS NOT NULL)
                    )"""
                )
        except (OSError, sqlite3.Error) as error:
            raise PaperStorageError("Could not initialise paper database.") from error

    def save(self, paper: StandardisedPaper) -> StoredPaper:
        """Insert or update a paper, matching either normalized identifier."""
        if not isinstance(paper, StandardisedPaper):
            raise TypeError("paper must be a StandardisedPaper instance.")
        normalized = _normalize_paper_identifiers(paper)
        if normalized.pmid is None and normalized.doi is None:
            raise PaperStorageError("A paper must have a PMID or DOI to be stored.")

        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                matches = self._matching_rows(
                    connection, normalized.pmid, normalized.doi
                )
                if len(matches) > 1:
                    raise PaperIdentityConflictError(
                        "PMID and DOI identify different stored papers."
                    )
                existing = matches[0] if matches else None
                if existing is not None:
                    self._check_identifier_conflict(existing, normalized)
                    connection.execute(
                        """UPDATE papers SET
                            pmid = COALESCE(pmid, ?), doi = COALESCE(doi, ?),
                            title = ?, authors_json = ?, journal = ?,
                            publication_year = ?, abstract_available = ?,
                            source_identifier = ?, source_url = ? WHERE id = ?""",
                        (*self._values(normalized), existing["id"]),
                    )
                else:
                    connection.execute(
                        """INSERT INTO papers (
                            pmid, doi, title, authors_json, journal,
                            publication_year, abstract_available,
                            source_identifier, source_url
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        self._values(normalized),
                    )
                row = self._matching_rows(
                    connection, normalized.pmid, normalized.doi
                )[0]
                return self._to_paper(row)
        except PaperStorageError:
            raise
        except sqlite3.IntegrityError as error:
            raise PaperIdentityConflictError(
                "A PMID or DOI conflicts with another stored paper."
            ) from error
        except sqlite3.Error as error:
            raise PaperStorageError("Could not save paper citation.") from error

    def get(
        self, *, pmid: str | None = None, doi: str | None = None
    ) -> StoredPaper | None:
        """Find a stored paper by exactly one PMID or DOI."""
        normalized_pmid, normalized_doi = _validate_lookup(pmid, doi)
        try:
            with closing(self._connect()) as connection, connection:
                rows = self._matching_rows(connection, normalized_pmid, normalized_doi)
                if len(rows) > 1:
                    raise PaperIdentityConflictError(
                        "Identifier matches multiple stored papers."
                    )
                return self._to_paper(rows[0]) if rows else None
        except PaperStorageError:
            raise
        except sqlite3.Error as error:
            raise PaperStorageError("Could not read paper citation.") from error

    def list_papers(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[StoredPaper, ...]:
        """Return stored papers in insertion order with pagination."""
        _validate_pagination(limit, offset)
        try:
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(
                    "SELECT * FROM papers ORDER BY id LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                return tuple(self._to_paper(row) for row in rows)
        except PaperStorageError:
            raise
        except sqlite3.Error as error:
            raise PaperStorageError("Could not list paper citations.") from error

    def search(
        self,
        query: str,
        *,
        limit: int = 100,
        offset: int = 0,
        year_from: int | None = None,
        year_to: int | None = None,
        abstract_available: bool | None = None,
        strategy: str = "keyword",
    ) -> tuple[StoredPaper, ...]:
        """Search papers by all keywords, with optional citation filters.

        Every whitespace-separated keyword must occur in the title, authors,
        or journal. Results rank title matches above author matches and journal
        matches, then retain insertion order for ties. Year bounds are inclusive.
        The ``fuzzy`` strategy also includes approximate title matches above
        the configured similarity threshold.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty text.")
        if not isinstance(strategy, str) or strategy not in {
            "keyword", "fuzzy", "boolean"
        }:
            raise ValueError("strategy must be 'keyword', 'fuzzy', or 'boolean'.")
        _validate_pagination(limit, offset)
        _validate_search_filters(year_from, year_to, abstract_available)
        keywords = tuple(dict.fromkeys(query.casefold().split()))
        boolean_expression = (
            _parse_boolean_query(query) if strategy == "boolean" else None
        )
        clauses: list[str] = []
        parameters: list[object] = []
        if year_from is not None:
            clauses.append("publication_year >= ?")
            parameters.append(year_from)
        if year_to is not None:
            clauses.append("publication_year <= ?")
            parameters.append(year_to)
        if abstract_available is not None:
            clauses.append("abstract_available = ?")
            parameters.append(int(abstract_available))
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(
                    f"SELECT * FROM papers {where_clause} ORDER BY id",
                    parameters,
                ).fetchall()
                ranked: list[tuple[int, float, sqlite3.Row]] = []
                for row in rows:
                    fields = _search_fields(row)
                    if boolean_expression is not None:
                        matched, score = _boolean_match_score(
                            boolean_expression, fields
                        )
                        if matched:
                            ranked.append((0, float(score), row))
                        continue
                    score = _keyword_relevance(fields, keywords)
                    if score is not None:
                        ranked.append((0, float(score), row))
                    elif strategy == "fuzzy":
                        fuzzy_score = _fuzzy_title_relevance(row["title"], keywords)
                        if fuzzy_score is not None:
                            ranked.append((1, fuzzy_score, row))
                ranked.sort(
                    key=lambda item: (item[0], -item[1], item[2]["id"])
                )
                page = ranked[offset : offset + limit]
                return tuple(self._to_paper(row) for _, _, row in page)
        except PaperStorageError:
            raise
        except sqlite3.Error as error:
            raise PaperStorageError("Could not search paper citations.") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "casefold", 1,
            lambda value: value.casefold() if isinstance(value, str) else "",
            deterministic=True,
        )
        return connection

    @staticmethod
    def _values(paper: StandardisedPaper) -> tuple[object, ...]:
        return (
            paper.pmid, paper.doi, paper.title, json.dumps(paper.authors),
            paper.journal, paper.publication_year, int(paper.abstract_available),
            paper.source_identifier, paper.source_url,
        )

    @staticmethod
    def _matching_rows(
        connection: sqlite3.Connection, pmid: str | None, doi: str | None
    ) -> list[sqlite3.Row]:
        matches: dict[int, sqlite3.Row] = {}
        if pmid is not None:
            row = connection.execute(
                "SELECT * FROM papers WHERE pmid = ?", (pmid,)
            ).fetchone()
            if row is not None:
                matches[row["id"]] = row
        if doi is not None:
            # Exact matches use the UNIQUE index; the other clauses also return
            # older mixed-case/prefixed/padded values so they remain discoverable.
            # shortcut: legacy DOIs differing only by non-ASCII case are missed;
            # normalise stored DOIs in place if such data ever appears.
            for row in connection.execute(
                """SELECT * FROM papers WHERE doi = ? OR doi NOT GLOB '10.*'
                    OR doi GLOB '*[A-Z]*'
                    OR doi <> rtrim(doi, char(9, 10, 11, 12, 13, 32))""",
                (doi,),
            ).fetchall():
                try:
                    stored_doi = _normalize_doi(row["doi"])
                except ValueError:
                    continue
                if stored_doi == doi:
                    matches[row["id"]] = row
        return list(matches.values())

    @staticmethod
    def _check_identifier_conflict(
        existing: sqlite3.Row, incoming: StandardisedPaper
    ) -> None:
        old_pmid = existing["pmid"]
        old_doi = _normalize_doi(existing["doi"]) if existing["doi"] else None
        if incoming.pmid and old_pmid and incoming.pmid != old_pmid:
            raise PaperIdentityConflictError(
                "PMID conflicts with the identifier of the matched paper."
            )
        if incoming.doi and old_doi and incoming.doi != old_doi:
            raise PaperIdentityConflictError(
                "DOI conflicts with the identifier of the matched paper."
            )

    @staticmethod
    def _to_paper(row: sqlite3.Row) -> StoredPaper:
        try:
            authors = json.loads(row["authors_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise PaperStorageError("Stored paper authors are invalid.") from error
        if not isinstance(authors, list) or any(
            not isinstance(author, str) for author in authors
        ):
            raise PaperStorageError("Stored paper authors are invalid.")
        return StoredPaper(
            pmid=row["pmid"], doi=row["doi"], title=row["title"],
            authors=tuple(authors), journal=row["journal"],
            publication_year=row["publication_year"],
            abstract_available=bool(row["abstract_available"]),
            source_identifier=row["source_identifier"], source_url=row["source_url"],
        )


_DOI_PATTERN = re.compile(r"10\.[0-9]{4,9}/\S+", re.ASCII)
_PMID_PATTERN = re.compile(r"[0-9]+", re.ASCII)
_TITLE_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
FUZZY_SIMILARITY_THRESHOLD = 0.85


def _normalize_paper_identifiers(paper: StandardisedPaper) -> StandardisedPaper:
    pmid = _normalize_pmid(paper.pmid) if paper.pmid is not None else None
    doi = _normalize_doi(paper.doi) if paper.doi is not None else None
    return replace(paper, pmid=pmid, doi=doi)


def _normalize_pmid(value: str) -> str:
    normalized = value.strip()
    if _PMID_PATTERN.fullmatch(normalized) is None:
        raise ValueError("PMID must contain ASCII digits only.")
    return normalized


def _normalize_doi(value: str) -> str:
    normalized = _strip_doi_prefix(value.strip())
    if _DOI_PATTERN.fullmatch(normalized) is None:
        raise ValueError("DOI must be a valid DOI identifier.")
    return normalized.casefold()


def _validate_lookup(
    pmid: str | None, doi: str | None
) -> tuple[str | None, str | None]:
    if (pmid is None) == (doi is None):
        raise ValueError("Provide exactly one of pmid or doi.")
    if pmid is not None:
        return _normalize_pmid(pmid), None
    return None, _normalize_doi(doi)


def _validate_pagination(limit: int, offset: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer.")


def _validate_search_filters(
    year_from: int | None,
    year_to: int | None,
    abstract_available: bool | None,
) -> None:
    for label, year in (("year_from", year_from), ("year_to", year_to)):
        if year is not None and (
            isinstance(year, bool) or not isinstance(year, int) or year < 1
        ):
            raise ValueError(f"{label} must be a positive integer when provided.")
    if year_from is not None and year_to is not None and year_from > year_to:
        raise ValueError("year_from must not be greater than year_to.")
    if abstract_available is not None and not isinstance(abstract_available, bool):
        raise ValueError("abstract_available must be True, False, or None.")


def _search_fields(row: sqlite3.Row) -> tuple[str, str, str]:
    """Return casefolded title, author names, and journal for matching.

    Authors are decoded from JSON so searches match names, not JSON escapes
    or punctuation; newlines stop phrases from spanning two authors.
    """
    try:
        authors = json.loads(row["authors_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise PaperStorageError("Stored paper authors are invalid.") from error
    if not isinstance(authors, list) or any(
        not isinstance(author, str) for author in authors
    ):
        raise PaperStorageError("Stored paper authors are invalid.")
    return (
        row["title"].casefold(),
        "\n".join(authors).casefold(),
        (row["journal"] or "").casefold(),
    )


def _keyword_relevance(
    fields: tuple[str, str, str], keywords: tuple[str, ...]
) -> int | None:
    """Score a record, or return None when any keyword is missing."""
    title, authors, journal = fields
    score = 0
    for keyword in keywords:
        if keyword in title:
            score += 3
        elif keyword in authors:
            score += 2
        elif keyword in journal:
            score += 1
        else:
            return None
    return score


def _parse_boolean_query(query: str) -> tuple[object, ...]:
    """Tokenize and parse explicit Boolean syntax; never infer operators."""
    tokens: list[_BooleanToken] = []
    position = 0
    for match in _BOOLEAN_TOKEN_PATTERN.finditer(query):
        if query[position : match.start()].strip():
            raise ValueError("Invalid character in Boolean search expression.")
        position = match.end()
        if match.group("operator") is not None:
            tokens.append(_BooleanToken("operator", match.group("operator").upper()))
        elif match.group("parenthesis") is not None:
            tokens.append(_BooleanToken("parenthesis", match.group("parenthesis")))
        elif match.group("phrase") is not None:
            phrase = match.group("phrase").strip()
            if not phrase:
                raise ValueError("Quoted Boolean search phrases must not be empty.")
            tokens.append(_BooleanToken("term", phrase))
        else:
            tokens.append(_BooleanToken("term", match.group("term")))
    if query[position:].strip():
        raise ValueError("Invalid character in Boolean search expression.")
    return _BooleanParser(tuple(tokens)).parse()


def _boolean_match_score(
    expression: tuple[object, ...],
    fields: tuple[str, str, str],
    negated: bool = False,
) -> tuple[bool, int]:
    """Evaluate the AST and score only terms in satisfied expression branches."""
    operator = expression[0]
    if operator == "term":
        term = expression[1]
        weights = [
            weight for value, weight in zip(fields, (3, 2, 1)) if term in value
        ]
        matched = bool(weights)
        if negated:
            return not matched, 0
        return matched, max(weights, default=0)
    if operator == "not":
        return _boolean_match_score(expression[1], fields, not negated)

    effective_operator = operator
    if negated:
        effective_operator = "or" if operator == "and" else "and"
    left_matched, left_score = _boolean_match_score(expression[1], fields, negated)
    right_matched, right_score = _boolean_match_score(expression[2], fields, negated)
    if effective_operator == "and":
        return (
            left_matched and right_matched,
            left_score + right_score if left_matched and right_matched else 0,
        )
    matched = left_matched or right_matched
    if not matched:
        return False, 0
    if left_matched and right_matched:
        return True, left_score + right_score
    return True, left_score if left_matched else right_score


def _fuzzy_title_relevance(
    title: str, keywords: tuple[str, ...]
) -> float | None:
    """Return score when keywords match distinct title tokens above threshold."""
    title_tokens = _TITLE_TOKEN_PATTERN.findall(title.casefold())
    if not title_tokens or len(keywords) > len(title_tokens):
        return None
    candidate_scores: list[list[tuple[int, float]]] = []
    for keyword in keywords:
        matches = []
        for index, token in enumerate(title_tokens):
            similarity = SequenceMatcher(None, keyword, token).ratio()
            if similarity >= FUZZY_SIMILARITY_THRESHOLD:
                matches.append((index, similarity))
        matches.sort(key=lambda item: (-item[1], item[0]))
        candidate_scores.append(matches)
    if any(not candidates for candidates in candidate_scores):
        return None

    # Match the most constrained keyword first and reserve each title token
    # for one keyword, avoiding inflated matches from a single similar word.
    candidate_scores.sort(key=lambda candidates: (len(candidates), -candidates[0][1]))

    def match(index: int, used_tokens: set[int], score_total: float) -> float | None:
        if index == len(candidate_scores):
            return score_total / len(candidate_scores)
        for token_index, similarity in candidate_scores[index]:
            if token_index in used_tokens:
                continue
            result = match(
                index + 1, used_tokens | {token_index}, score_total + similarity
            )
            if result is not None:
                return result
        return None

    return match(0, set(), 0.0)


