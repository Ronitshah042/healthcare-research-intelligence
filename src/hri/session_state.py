"""In-memory helpers for a deployed app whose database is read-only.

Saved lists and question quotas live in the app's session (for example
Streamlit's ``st.session_state``) and reset when the session or app restarts.
Re-exported by ``hri.services``; the contract is in ``docs/SERVICE_API.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import InvalidRequestError
from .export import EXPORT_FORMATS, papers_to_citations, papers_to_csv, papers_to_plain

__all__ = ["SessionSavedLists", "VisitorQuota", "DEFAULT_VISITOR_QUESTIONS"]

DEFAULT_VISITOR_QUESTIONS = 5
_MAX_NAME_LENGTH = 100


@dataclass
class SessionSavedLists:
    """Saved lists kept in memory: create, rename, delete, add, remove, export.

    Papers are looked up in the (read-only) database with ``get_paper``.
    Nothing is written to disk; lists reset when the session ends.
    """

    database_path: str | Path | None = None
    _lists: dict[str, list] = field(default_factory=dict)

    def names(self) -> tuple[str, ...]:
        return tuple(self._lists)

    def create(self, name: str) -> str:
        name = _check_name(name)
        if self._find(name) is not None:
            raise InvalidRequestError(f"A list called {name!r} already exists.")
        self._lists[name] = []
        return name

    def rename(self, name: str, new_name: str) -> str:
        old = self._require(name)
        new_name = _check_name(new_name)
        clash = self._find(new_name)
        if clash is not None and clash != old:
            raise InvalidRequestError(f"A list called {new_name!r} already exists.")
        self._lists = {(new_name if key == old else key): value
                       for key, value in self._lists.items()}
        return new_name

    def delete(self, name: str) -> None:
        del self._lists[self._require(name)]

    def add(self, name: str, identifier: str):
        """Add a stored paper by PMID or DOI; returns it. Adding twice is a no-op."""
        from .services import get_paper  # avoids a circular import

        papers = self._lists[self._require(name)]
        paper = get_paper(identifier, database_path=self.database_path)
        if all(_key(existing) != _key(paper) for existing in papers):
            papers.append(paper)
        return paper

    def remove(self, name: str, identifier: str) -> bool:
        """Remove a paper by PMID or DOI; returns whether it was in the list."""
        papers = self._lists[self._require(name)]
        wanted = identifier.strip().lower()
        kept = [p for p in papers if wanted not in {(p.pmid or "").lower(), (p.doi or "").lower()}]
        self._lists[self._require(name)] = kept
        return len(kept) != len(papers)

    def papers(self, name: str) -> tuple:
        return tuple(self._lists[self._require(name)])

    def export(self, name: str, export_format: str = "csv") -> str:
        """The list as CSV, Vancouver citations, or plain text, like ``export_list``."""
        if export_format not in EXPORT_FORMATS:
            raise InvalidRequestError(
                f"export_format must be one of: {', '.join(EXPORT_FORMATS)}."
            )
        papers = self.papers(name)
        render = {"csv": papers_to_csv, "citations": papers_to_citations,
                  "plain": papers_to_plain}[export_format]
        return render(papers)

    def _find(self, name: str) -> str | None:
        wanted = name.casefold()
        return next((key for key in self._lists if key.casefold() == wanted), None)

    def _require(self, name: str) -> str:
        found = self._find(_check_name(name))
        if found is None:
            raise InvalidRequestError(f"No list called {name!r}.")
        return found


@dataclass
class VisitorQuota:
    """A per-visitor limit on AI questions, kept in the visitor's session."""

    limit: int = DEFAULT_VISITOR_QUESTIONS
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def try_use(self) -> bool:
        """Use one question if any remain; returns whether it was allowed."""
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def _check_name(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise InvalidRequestError("List name must be non-empty text.")
    if len(name.strip()) > _MAX_NAME_LENGTH:
        raise InvalidRequestError(f"List name must be at most {_MAX_NAME_LENGTH} characters.")
    return name.strip()


def _key(paper) -> tuple[str, str]:
    return ((paper.pmid or "").lower(), (paper.doi or "").lower())
