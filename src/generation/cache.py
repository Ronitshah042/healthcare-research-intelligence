"""Answer cache and daily request counter, to stay inside free-tier limits.

Both live in the SQLite database. When the database cannot be written (for
example a read-only deployed bundle), they fall back to process memory.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from generation.llm import LLMError, LLMReply


DEFAULT_DAILY_REQUEST_CAP = 100


class DailyCapReachedError(LLMError):
    """The configured number of AI requests for today has been used."""


class AnswerCache:
    """Cached model replies and per-day request counts."""

    def __init__(self, database_path: str | Path, *, daily_cap: int) -> None:
        self._path = str(database_path)
        self.daily_cap = daily_cap
        self._memory: dict[str, LLMReply] = {}
        self._usage: dict[str, int] = {}
        self.in_memory = False

    def get(self, key: str) -> LLMReply | None:
        if self.in_memory:
            return self._memory.get(key)
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT reply_text, provider, model FROM answer_cache WHERE cache_key = ?",
                    (key,),
                ).fetchone()
        except sqlite3.Error:
            return self._memory.get(key)
        return LLMReply(*row) if row else self._memory.get(key)

    def put(self, key: str, reply: LLMReply) -> None:
        self._write(
            lambda connection: connection.execute(
                """INSERT OR REPLACE INTO answer_cache
                    (cache_key, reply_text, provider, model, created_at)
                    VALUES (?, ?, ?, ?, ?)""",
                (key, reply.text, reply.provider, reply.model, _now().isoformat()),
            ),
            lambda: self._memory.__setitem__(key, reply),
        )

    def count_request(self) -> None:
        """Record one AI request today, or raise if the cap is already used."""
        day = _now().date().isoformat()
        if self.requests_today() >= self.daily_cap:
            raise DailyCapReachedError("Today's AI request limit has been reached.")
        self._write(
            lambda connection: connection.execute(
                """INSERT INTO llm_usage (day, requests) VALUES (?, 1)
                    ON CONFLICT (day) DO UPDATE SET requests = requests + 1""",
                (day,),
            ),
            lambda: self._usage.__setitem__(day, self._usage.get(day, 0) + 1),
        )

    def requests_today(self) -> int:
        day = _now().date().isoformat()
        if not self.in_memory:
            try:
                with closing(self._connect()) as connection:
                    row = connection.execute(
                        "SELECT requests FROM llm_usage WHERE day = ?", (day,)
                    ).fetchone()
                return (row[0] if row else 0) + self._usage.get(day, 0)
            except sqlite3.Error:
                pass
        return self._usage.get(day, 0)

    def _write(self, statement, fallback) -> None:
        if not self.in_memory:
            try:
                with closing(self._connect()) as connection, connection:
                    statement(connection)
                return
            except sqlite3.OperationalError:
                # Read-only or locked database: keep going in memory.
                self.in_memory = True
        fallback()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)


def _now() -> datetime:
    return datetime.now(timezone.utc)
