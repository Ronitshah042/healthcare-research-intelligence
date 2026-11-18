# CLAUDE.md

@AGENTS.md

`AGENTS.md` holds the project rules and takes precedence. This file adds the
commands and code facts needed to work here.

## Before changing anything
- Present a review first (goal, files to change, proposed content or diff), then
  wait for approval before saving, editing, or running anything that changes state.
- Implement only the approved milestone. No next milestone is approved until the
  user says so.
- Never commit or push without explicit permission.

## Commands
There is no `python` command here; use `python3`. No install step is needed.

```bash
# Full test suite
PYTHONPATH=src python3 -m unittest discover -s tests -v

# One test module
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_storage.py' -v

# Whitespace check on pending changes
git diff --check
```

The suite fails with `No module named 'ingestion'` if `PYTHONPATH=src` is missing.
"Retrying a transient PubMed request failure." in test output is expected logging.

## Current state
`src/ingestion` holds the data layers and `src/hri` the application layer.
`src/retrieval`, `src/generation`, and `src/evaluation` are empty placeholders. See `docs/PROJECT_LOG.md` for history.

Dependency direction, lowest layer first:

| Module | Responsibility |
|---|---|
| `http.py` | Shared rate limiting, retries, and error redaction for every API client |
| `identifiers.py` | Shared DOI-prefix and PMID leading-zero clean-up; imports nothing from the project |
| `metadata.py` | `PaperMetadata` and validation; imports nothing from the project |
| `pubmed_config.py` | `PubMedClientConfig` and environment variables |
| `pubmed.py` | `PubMedClient`: query validation, XML mapping (HTTP via `http.py`) |
| `pipeline.py` | `SearchPipeline`: thin routing, `StandardisedPaper`, deduplication |
| `schema.py` | Versioned SQLite layout and upgrades (`PRAGMA user_version`) |
| `storage.py` | `PaperStore`: SQLite persistence, local search, and statistics |
| `saved_lists.py` | `SavedListStore`: named lists of stored papers |
| `licence_policy.py` | `LicencePolicy`: the `HRI_ALLOWED_LICENCES` allowlist |
| `pmc_client.py` | `PmcClient`: PMC search, ID converter, Cloud metadata and XML |
| `fulltext.py` | Parse JATS XML into canonical sections; drop references, figures, tables |
| `chunking.py` | Split sections into ~400-token passages, never across sections |
| `fulltext_store.py` | `FullTextStore`: documents, sections, chunks, rejections, FTS5 passage search |

`src/hri` sits on top and must stay free of business logic in the CLI:

| Module | Responsibility |
|---|---|
| `config.py` | Database path from `--db`, `HRI_DB_PATH`, or the default |
| `export.py` | CSV and plain-citation rendering |
| `errors.py` | `InvalidRequestError`, shared by the service modules |
| `services.py` | UI-agnostic functions returning dataclasses; see `docs/SERVICE_API.md` |
| `fulltext_services.py` | Full-text fetch, recheck, stats, and passage search; re-exported by `services` |
| `cli.py` | `hri` argparse commands; imports only `services` |

Public names are exported from `src/ingestion/__init__.py`; add new ones there.

## Rules that are easy to break
**Source content**
- `docs/CONTENT_POLICY.md` is the source of truth for stored text. Read it
  before touching ingestion of any text.
- PubMed abstracts are never kept: `PaperMetadata.abstract` stays `None` for
  PubMed records, and only the `abstract_available` boolean is stored.
- Full text may be stored only for PMC Open Access articles whose
  `license_code` is on `HRI_ALLOWED_LICENCES` (default `CC0,CC BY`), only the
  current version, and only from the PMC Cloud Service. The licence is checked
  before the text is downloaded. Retracted articles are rejected; corrections
  and concerns are stored with `notice_flags`.
- The abstract inside an allowed PMC article is stored as its `abstract`
  section. Never copy it onto the PubMed citation record.
- Rejected articles leave only a `fulltext_rejections` row, never text.
- Never store raw API responses. Widening the licence list or adding a text
  source needs the owner's explicit approval.
- PubMed-derived results must keep `PUBMED_ATTRIBUTION`; any UI must display it.
  Stored papers derive it from `source_name`, so keep that set to `PUBMED_SOURCE_NAME`.

**Secrets and requests**
- The PubMed client's `base_url` is restricted to HTTPS on
  `eutils.ncbi.nlm.nih.gov`. Do not loosen this; tests inject a transport
  instead of using a mock server.
- The PMC client has a fixed HTTPS allowlist: `eutils.ncbi.nlm.nih.gov`,
  `pmc.ncbi.nlm.nih.gov` (ID converter), and `pmc-oa-opendata.s3.amazonaws.com`.
  `tool`, `email`, and the API key are sent to NCBI hosts only, never to S3.
- Do not put NCBI `<ERROR>` text, API keys, or request URLs in exceptions or logs.
- The rate-limit scheduler is shared per process and keeps only a digest of the
  API key. Never hold its lock during HTTP calls. Every retry takes a new slot.

**Search behaviour**
- `search_title` is the only method with a fallback (`[ti]`, then `[tiab]` once).
- PubMed plain-text methods reject query syntax. Advanced queries need approval.
- `total_count` is PubMed's overall count; `normalised_count` is the current page.

**Application layer**
- `cli.py` calls only `hri.services`. A future UI or API must do the same.
- Service functions return typed objects and never print. Changing a signature,
  result field, or error means updating `docs/SERVICE_API.md` in the same change.
- No UI code or UI design in this repository.
- `requirements.txt` and `pyproject.toml` must list the same dependencies.
- Changing the database layout means a new step in `schema.py` and a higher
  `SCHEMA_VERSION`, with a test that upgrades the previous layout.

**Paper identity**
- Match papers only on PMID or DOI, never on title similarity.
- `PaperStore.save` raises `PaperIdentityConflictError` on contradictory IDs.
- `save` fills in a missing identifier but overwrites all other fields with the
  incoming values, including `None`. The exception is `source_name`: an incoming
  `None` keeps the stored source, so attribution is never lost on update.
- Publication year is `PubDate/Year`, then the first year in `MedlineDate`. Never
  substitute `ArticleDate` or history dates.

## Code conventions
- Standard library only. Adding any dependency needs approval.
- Stay compatible with Python 3.9 until a minimum version is chosen: use
  `from __future__ import annotations`, and no `dataclass(slots=True)`.
- Use frozen dataclasses for records and specific exception classes for errors.
- Use British spelling in new names (`normalise`); `storage.py` is an older exception.
- Library code takes local data paths from the caller. The one approved default
  is the `hri` application database, `data/hri.sqlite3` (overridden by
  `HRI_DB_PATH` or `--db`), which Git ignores. Never commit databases or downloads.

## Tests
- Use `unittest`. Tests must never call live services or sleep for real time.
- Inject `transport`, `clock`, and `sleeper` into `PubMedClient` and `PmcClient`;
  use a fake client for pipeline and service tests and a temporary directory for
  storage tests. PMC tests use the recorded fixtures in `tests/fixtures/pmc/`.
- Reset shared limiter state with `PubMedClient._reset_process_rate_limiters_for_tests()`.
- Add a regression test with every bug fix.

## Docs to update with each milestone
- `docs/PROJECT_LOG.md`: always, with the exact test result. Never claim tests passed
  unless they ran.
- `docs/DECISIONS.md`: for design decisions and trade-offs.
- `docs/PUBMED_INTEGRATION.md`: when the PubMed client's behaviour changes.
- `docs/LEARNING_NOTES.md`: beginner-friendly explanations of new concepts.
- `README.md`: when project capabilities or status change.
