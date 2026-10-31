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
Only `src/ingestion` contains code. `src/retrieval`, `src/generation`, and
`src/evaluation` are empty placeholders. See `docs/PROJECT_LOG.md` for history.

Dependency direction, lowest layer first:

| Module | Responsibility |
|---|---|
| `metadata.py` | `PaperMetadata` and validation; imports nothing from the project |
| `pubmed_config.py` | `PubMedClientConfig` and environment variables |
| `pubmed.py` | `PubMedClient`: query validation, rate limits, retries, XML mapping |
| `pipeline.py` | `SearchPipeline`: thin routing, `StandardisedPaper`, deduplication |
| `storage.py` | `PaperStore`: SQLite persistence and local search |

Public names are exported from `src/ingestion/__init__.py`; add new ones there.

## Rules that are easy to break
**Source content**
- Abstract text is never kept: `PaperMetadata.abstract` stays `None` for PubMed
  records, and only the `abstract_available` boolean is stored.
- Do not fetch full text or store API responses. Changing this needs a rights
  review and explicit approval.
- PubMed-derived results must keep `PUBMED_ATTRIBUTION`; any UI must display it.

**Secrets and requests**
- `base_url` is restricted to HTTPS on `eutils.ncbi.nlm.nih.gov`. Do not loosen
  this; tests inject a transport instead of using a mock server.
- Do not put NCBI `<ERROR>` text, API keys, or request URLs in exceptions or logs.
- The rate-limit scheduler is shared per process and keeps only a digest of the
  API key. Never hold its lock during HTTP calls. Every retry takes a new slot.

**Search behaviour**
- `search_title` is the only method with a fallback (`[ti]`, then `[tiab]` once).
- PubMed plain-text methods reject query syntax. Advanced queries need approval.
- `total_count` is PubMed's overall count; `normalised_count` is the current page.

**Paper identity**
- Match papers only on PMID or DOI, never on title similarity.
- `PaperStore.save` raises `PaperIdentityConflictError` on contradictory IDs.
- `save` fills in a missing identifier but overwrites all other fields with the
  incoming values, including `None`.
- Publication year is `PubDate/Year`, then the first year in `MedlineDate`. Never
  substitute `ArticleDate` or history dates.

## Code conventions
- Standard library only. Adding any dependency needs approval.
- Stay compatible with Python 3.9 until a minimum version is chosen: use
  `from __future__ import annotations`, and no `dataclass(slots=True)`.
- Use frozen dataclasses for records and specific exception classes for errors.
- Use British spelling in new names (`normalise`); `storage.py` is an older exception.
- Local data paths are supplied by the caller. Never write databases or downloads
  into the repository by default.

## Tests
- Use `unittest`. Tests must never call live services or sleep for real time.
- Inject `transport`, `clock`, and `sleeper` into `PubMedClient`; use a fake client
  for pipeline tests and a temporary directory for storage tests.
- Reset shared limiter state with `PubMedClient._reset_process_rate_limiters_for_tests()`.
- Add a regression test with every bug fix.

## Docs to update with each milestone
- `docs/PROJECT_LOG.md`: always, with the exact test result. Never claim tests passed
  unless they ran.
- `docs/DECISIONS.md`: for design decisions and trade-offs.
- `docs/PUBMED_INTEGRATION.md`: when the PubMed client's behaviour changes.
- `docs/LEARNING_NOTES.md`: beginner-friendly explanations of new concepts.
- `README.md`: when project capabilities or status change.
