"""Where the application keeps its local database."""

from __future__ import annotations

from collections.abc import Mapping
from os import environ
from pathlib import Path

DATABASE_PATH_VARIABLE = "HRI_DB_PATH"
# Relative to the current working directory; SQLite files are ignored by Git.
DEFAULT_DATABASE_PATH = Path("data") / "hri.sqlite3"


def resolve_database_path(
    explicit: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the explicit path, else ``HRI_DB_PATH``, else the default."""
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser()
    values = environ if environment is None else environment
    configured = values.get(DATABASE_PATH_VARIABLE, "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_DATABASE_PATH
