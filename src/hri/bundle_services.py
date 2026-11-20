"""Build, fetch, and check the read-only data bundle the deployed app uses.

A bundle is one SQLite file (citations, licensed passages, vectors) plus a
manifest JSON. Re-exported by ``hri.services``; see ``docs/DEPLOYMENT.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ingestion.licence_policy import LicencePolicy
from ingestion.schema import SCHEMA_VERSION
from ingestion.storage import PaperStore
from retrieval.embeddings import MODEL_ID, Embedder

from .errors import InvalidRequestError

__all__ = [
    "BUNDLE_NAME", "MANIFEST_NAME", "BUNDLE_LICENCES", "BundleResult",
    "DeploymentCheck", "build_bundle", "fetch_bundle", "check_deployment",
    "bundle_directory", "load_topics",
]

BUNDLE_NAME = "hri-bundle.sqlite3"
MANIFEST_NAME = "hri-bundle.manifest.json"
# A public bundle redistributes text: only licences that allow that, whatever
# HRI_ALLOWED_LICENCES says for local use.
BUNDLE_LICENCES = ("CC0", "CC BY")
DOWNLOAD_HOSTS = ("github.com", ".githubusercontent.com")
MAX_TOPICS = 20
MAX_CITATIONS_PER_TOPIC = 200
MAX_FULLTEXT_PER_TOPIC = 50


@dataclass(frozen=True)
class BundleResult:
    bundle_path: Path
    manifest_path: Path
    size_bytes: int
    manifest: dict


@dataclass(frozen=True)
class DeploymentCheck:
    """The startup self-check: ``ok`` only when every problem list is empty."""

    ok: bool
    bundle_path: Path
    manifest: dict | None
    problems: tuple[str, ...]


def load_topics(path: str | Path) -> list[dict]:
    """Read ``topics.json``: ``{"format": 1, "topics": [...]}``."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise InvalidRequestError(f"Cannot read topics file {path}: {error}") from None
    topics = data.get("topics") if isinstance(data, dict) and data.get("format") == 1 else None
    if not isinstance(topics, list) or not 1 <= len(topics) <= MAX_TOPICS:
        raise InvalidRequestError(
            f"The topics file needs \"format\": 1 and 1 to {MAX_TOPICS} topics."
        )
    checked = []
    for topic in topics:
        try:
            name, query = str(topic["name"]).strip(), str(topic["query"]).strip()
            citations = int(topic.get("max_citations", 40))
            fulltext = int(topic.get("max_fulltext", 20))
        except (KeyError, TypeError, ValueError):
            raise InvalidRequestError(f"Invalid topic entry: {topic!r:.80}") from None
        if not name or not query or not 1 <= citations <= MAX_CITATIONS_PER_TOPIC \
                or not 0 <= fulltext <= MAX_FULLTEXT_PER_TOPIC:
            raise InvalidRequestError(f"Invalid topic entry: {topic!r:.80}")
        checked.append({"name": name, "query": query, "max_citations": citations,
                        "max_fulltext": fulltext,
                        "year_from": topic.get("year_from")})
    return checked


def build_bundle(
    topics_path: str | Path,
    *,
    output_dir: str | Path = "dist",
    work_database: str | Path | None = None,
    pubmed_client=None,
    pmc_client=None,
    embedder: Embedder | None = None,
    now: Callable[[], datetime] | None = None,
) -> BundleResult:
    """Ingest, fetch licensed full text, re-check retractions, embed, and package.

    Work happens in ``work_database`` (default ``<output_dir>/build.sqlite3``);
    the bundle is a compacted copy without caches, saved lists, rejection
    records, or per-section text, in rollback-journal mode so it can be opened
    read-only.
    """
    from .services import (
        embed_passages,
        fetch_fulltext,
        get_fulltext_stats,
        ingest,
        recheck_fulltext,  # avoids a circular import
    )

    topics = load_topics(topics_path)
    clock = now or (lambda: datetime.now(timezone.utc))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    work = Path(work_database) if work_database else output / "build.sqlite3"
    policy = LicencePolicy.of(BUNDLE_LICENCES)
    log = []
    for topic in topics:
        ingested = ingest(topic["query"], max_records=topic["max_citations"],
                          year_from=topic["year_from"], database_path=work,
                          client=pubmed_client)
        fetched = None
        if topic["max_fulltext"]:
            fetched = fetch_fulltext(query=topic["query"], max_articles=topic["max_fulltext"],
                                     database_path=work, client=pmc_client, policy=policy,
                                     now=clock)
        log.append({"topic": topic["name"], "query": topic["query"],
                    "citations_saved": ingested.saved,
                    "fulltext_stored": fetched.stored if fetched else 0})
    recheck = recheck_fulltext(database_path=work, client=pmc_client, now=clock)
    embedded = embed_passages(database_path=work, embedder=embedder)
    rejections = dict(get_fulltext_stats(database_path=work).rejections_by_reason)

    bundle = output / BUNDLE_NAME
    temporary = output / (BUNDLE_NAME + ".tmp")
    temporary.unlink(missing_ok=True)
    with closing(sqlite3.connect(work)) as source:
        source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        source.execute("VACUUM INTO ?", (str(temporary),))
    with closing(sqlite3.connect(temporary)) as connection:
        for table in ("answer_cache", "llm_usage", "saved_list_items", "saved_lists",
                      "fulltext_rejections", "document_sections"):
            connection.execute(f"DELETE FROM {table}")
        connection.commit()
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("VACUUM")
        counts = {name: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for name, table in (("papers", "papers"), ("articles", "documents"),
                                      ("passages", "chunks"),
                                      ("vectors", "chunk_embeddings"))}
        licences = dict(connection.execute(
            "SELECT licence, COUNT(*) FROM documents GROUP BY licence ORDER BY licence"))
        models = [row[0] for row in connection.execute(
            "SELECT DISTINCT model FROM chunk_embeddings")]
    temporary.replace(bundle)
    manifest = {
        "format": 1,
        "built_at": clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": SCHEMA_VERSION,
        "embedding_model": models[0] if len(models) == 1 else models,
        "counts": counts,
        "licences": licences,
        "allowed_licences": list(BUNDLE_LICENCES),
        "rejections": rejections,
        "retraction_recheck": {"checked": recheck.checked,
                               "removed": list(recheck.removed_retracted)},
        "embedding": {"embedded": embedded.embedded,
                      "over_token_limit": embedded.over_token_limit},
        "topics": log,
        "attribution": "Citations: PubMed, U.S. National Library of Medicine (NLM). "
                       "Full text: PubMed Central Open Access subset; each article "
                       "keeps its own licence and attribution.",
        "sha256": _sha256(bundle),
        "size_bytes": bundle.stat().st_size,
    }
    manifest_path = output / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return BundleResult(bundle, manifest_path, bundle.stat().st_size, manifest)


def fetch_bundle(
    base_url: str,
    *,
    directory: str | Path = Path("data") / "bundle",
    opener: Callable[[str], bytes] | None = None,
) -> Path:
    """Download the manifest and bundle once, verify the SHA-256, and cache them.

    ``base_url`` is the release download folder, for example
    ``https://github.com/<owner>/<repo>/releases/latest/download/``. A cached
    bundle whose checksum matches the current manifest is reused.
    """
    fetch = opener or _download
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    base = base_url.rstrip("/") + "/"
    _check_host(base)
    manifest = json.loads(fetch(base + MANIFEST_NAME))
    expected = str(manifest.get("sha256", ""))
    bundle = folder / BUNDLE_NAME
    if bundle.exists() and _sha256(bundle) == expected:
        (folder / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
        return bundle
    partial = folder / (BUNDLE_NAME + ".part")
    partial.write_bytes(fetch(base + BUNDLE_NAME))
    if _sha256(partial) != expected:
        partial.unlink()
        raise InvalidRequestError("The downloaded bundle failed its SHA-256 check.")
    partial.replace(bundle)
    (folder / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    return bundle


def check_deployment(bundle_path: str | Path, *, embedder_id: str = MODEL_ID) -> DeploymentCheck:
    """Bundle present and intact, layout current, and built with this embedder."""
    bundle = Path(bundle_path)
    manifest_path = bundle.with_name(MANIFEST_NAME)
    problems: list[str] = []
    manifest = None
    if not bundle.is_file():
        problems.append(f"Bundle missing: {bundle}.")
    if not manifest_path.is_file():
        problems.append(f"Manifest missing: {manifest_path}.")
    else:
        try:
            manifest = json.loads(manifest_path.read_text())
        except ValueError:
            problems.append("Manifest is not valid JSON.")
    if manifest is not None:
        if manifest.get("schema_version") != SCHEMA_VERSION:
            problems.append(f"Bundle layout {manifest.get('schema_version')} does not match "
                            f"this code ({SCHEMA_VERSION}); rebuild the bundle.")
        if manifest.get("embedding_model") != embedder_id:
            problems.append(f"Bundle vectors come from {manifest.get('embedding_model')!r}, "
                            f"but the app uses {embedder_id!r}; rebuild the bundle.")
        if bundle.is_file() and manifest.get("sha256") != _sha256(bundle):
            problems.append("Bundle checksum does not match its manifest.")
    if bundle.is_file() and not problems:
        try:
            PaperStore(bundle, read_only=True)
        except Exception as error:  # report, never crash the app at start-up
            problems.append(f"Bundle cannot be opened read-only: {error}")
    return DeploymentCheck(not problems, bundle, manifest, tuple(problems))


class _GitHubOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_host(url: str) -> None:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not any(
        host == allowed or (allowed.startswith(".") and host.endswith(allowed))
        for allowed in DOWNLOAD_HOSTS
    ):
        raise InvalidRequestError("Bundles are downloaded only from GitHub over HTTPS.")


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "healthcare-research-intelligence"})
    with build_opener(_GitHubOnly()).open(request, timeout=120) as response:
        return response.read()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def bundle_directory() -> Path:
    """``HRI_BUNDLE_DIR``, else ``data/bundle``."""
    return Path(os.environ.get("HRI_BUNDLE_DIR", "").strip() or Path("data") / "bundle")

