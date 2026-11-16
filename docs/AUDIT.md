# Repository Audit (Phase 0)

**Date:** 2026-11-16 · **Commit audited:** `6e61bc5` · **Scope:** audit only, no
new features.

## 1. Test results

Command: `PYTHONPATH=src python3 -m unittest discover -s tests -v`

| Interpreter | Result |
| --- | --- |
| Python 3.15.2 | Ran 118 tests, OK (0 failures, 0 errors, 0 skipped) |
| Python 3.9.6 | Ran 118 tests, OK (0 failures, 0 errors, 0 skipped) |
| Python 3.15.2, fresh `git clone` | Ran 118 tests, OK |

Tests per file: `test_pubmed.py` 49, `test_storage.py` 44, `test_metadata.py`
17, `test_pipeline.py` 8. No test calls a live service or sleeps in real time.

Line coverage, measured with the standard-library `trace` module (it also
counts continuation lines of multi-line signatures as missed, so real coverage
is slightly higher):

| Module | Coverage |
| --- | --- |
| `metadata.py` | 100% |
| `pipeline.py` | 97% |
| `pubmed_config.py` | 93% |
| `pubmed.py` | 91% |
| `storage.py` | 88% |

## 2. Expected functionality compared with the code

### Implemented and tested

| Feature | Where | Evidence (test) |
| --- | --- | --- |
| Five search modes: title, title/abstract, author, DOI, PMID | `pubmed.py` | `test_search_modes_build_documented_field_queries` |
| Title fallback from `[ti]` to `[tiab]`, once, title mode only | `pubmed.py` | `test_title_search_falls_back_…`, `test_author_and_identifier_searches_do_not_fall_back` |
| Pagination through the NCBI history server, page size capped at 20 | `pubmed.py` | `test_pagination_uses_history_server_…`, `test_out_of_range_page_…` |
| Year filter, single year and range | `pubmed.py` | `test_single_publication_year_uses_one_year_filter`, `test_doi_lookup_retains_a_year_filter` |
| Plain-text searches reject PubMed query syntax | `pubmed.py` | `test_plain_text_searches_reject_pubmed_query_syntax` |
| Rate limiting, shared per process and per API key | `pubmed.py` | five `test_rate_limiter_…` tests, including a concurrency test |
| Bounded retries with exponential backoff and `Retry-After` | `pubmed.py` | `test_retries_…`, `test_stops_retrying_after_the_configured_limit`, `test_honors_numeric_retry_after_for_http_429`, `test_ignores_unusable_retry_after_values` |
| HTTPS restricted to the official NCBI host | `pubmed_config.py` | `test_requires_the_official_secure_eutilities_host` |
| Error redaction: NCBI `<ERROR>` text and API key kept out of exceptions | `pubmed.py` | `test_translates_ncbi_xml_error_payloads_without_exposing_api_keys` |
| `PaperMetadata` validation and normalisation | `metadata.py` | all 17 tests in `test_metadata.py` |
| Publication year from `PubDate/Year`, then `MedlineDate`, never other dates | `pubmed.py` | `test_primary_journal_year_wins_…`, `test_uses_first_medline_date_year_…` |
| Invalid records skipped and counted | `pubmed.py`, `pipeline.py` | `test_skips_invalid_citation_and_keeps_valid_siblings`, `test_skips_invalid_records_without_losing_valid_records` |
| Deduplication by PMID/DOI only, contradictory IDs kept apart | `pipeline.py` | `test_merges_duplicates_…`, `test_does_not_merge_records_with_different_pmids_sharing_a_doi`, `test_keeps_unlinked_partial_identifiers_separate` |
| Abstract text not kept; only `abstract_available` | `pubmed.py`, `storage.py` | `test_maps_pubmed_record_to_validated_metadata_without_abstract`; the database has no abstract-text column |
| SQLite save, lookup, and update | `storage.py` | `test_saves_and_reads_…`, `test_saves_again_as_update_…`, `test_enriches_record_with_identifier_arriving_later` |
| `PaperIdentityConflictError` on contradictory identifiers | `storage.py` | three `test_conflicting_…` tests |
| Keyword search with ranking, year and abstract filters, pagination | `storage.py` | `test_search_…` tests |
| Fuzzy title search | `storage.py` | eight `test_fuzzy_…` tests |
| Boolean search: `AND`/`OR`/`NOT`, grouping, phrases | `storage.py` | twelve `test_boolean_…` tests |

### Implemented but untested

| Item | Where | Risk |
| --- | --- | --- |
| **`PUBMED_ATTRIBUTION` is on every `PubMedSearchPage` and `SearchResult`, but no test asserts it** | `pubmed.py`, `pipeline.py` | **Medium.** A regression that drops attribution would pass the suite. |
| Year filter rejects non-integer and out-of-range years | `pubmed.py:621-624` | Low |
| Blank DOI or PMID returns an empty page without a request | `pubmed.py:579,590` | Low |
| `PaperStore` rejects invalid `limit`, `offset`, year types, and invalid PMID/DOI in `get` | `storage.py:393-430` | Low |
| SQLite failure wrapping for save, get, list, and search | `storage.py:178-218,293` | Low; hard to trigger |
| Legacy stored DOI that cannot be normalised is skipped | `storage.py:338` | Low |
| Config rejects non-text and non-numeric values passed directly | `pubmed_config.py:115-156` | Low |
| Pipeline discards a record with no title or a non-integer year | `pipeline.py:146,155` | Low; defensive |
| The real network transport (`urlopen`) | `pubmed.py:514-516` | Not unit-testable by design; verified by a manual live check on 2026-11-06 |

### Missing

| Item | Detail |
| --- | --- |
| **Attribution on stored results** | `StoredPaper` (returned by `PaperStore.get`, `list_papers`, `search`) has no attribution or source-name field, so results read back from SQLite lose `PUBMED_ATTRIBUTION`. Only live search results carry it. |
| Dependency file | None existed. Fixed in this phase (`requirements.txt`). |
| `.env.example` | None existed. Fixed in this phase. |
| Packaging | No `pyproject.toml`; the package cannot be installed and needs `PYTHONPATH=src`. |
| Continuous integration, linter, type-checker configuration | None. |
| Licence file | None. |
| Declared minimum Python version | Compatible with 3.9 by convention only. |

### Bugs found

None were fixed during the audit. **All four were fixed afterwards**, on 2026-11-16, each with regression tests; see `PROJECT_LOG.md` ("Audit bug fixes"). The table records the behaviour at the audited commit.

| ID | Severity | Bug | Reproduced |
| --- | --- | --- | --- |
| B1 | **High** | **Book records are silently dropped.** EFetch returns books and book chapters (for example StatPearls) as `<PubmedBookArticle>`, but the client reads only `<PubmedArticle>`. A book in a page disappears without being counted in `invalid_record_count`, so a page shows fewer records than `total_count` implies. A page containing only books raises `PubMedResponseError`. | Yes: 1 article + 1 book returned 1 record, 0 invalid, total 2; a book-only page raised the error. |
| B2 | Medium | **Papers without listed authors are rejected.** `PaperMetadata` requires at least one author, so real PubMed records with no author list (some editorials, news items, older records) are counted as invalid and never returned. | Yes: an authorless article gave 0 records, 1 invalid. |
| B3 | Low | **`lookup_doi` rejects `doi:` and `https://doi.org/` prefixes** that the pipeline and `PaperStore` accept. | Yes |
| B4 | Low | **PMIDs with leading zeros are different papers.** `123` and `0123` are stored as two rows. PubMed does not emit leading zeros, so this needs hand-entered data. | Yes |

## 3. Design observations (not bugs)

- **Local search reads every matching row into memory** and ranks in Python on
  each call. Fine for thousands of papers; it will slow down beyond that.
- **Fuzzy matching can be slow in the worst case** (backtracking over many
  keywords against long titles).
- **`PaperStore.save` overwrites every non-identifier field**, including with
  `None`, so a sparser later record erases earlier data. Documented behaviour.
- **`PaperMetadata.abstract` still exists** and `normalise_paper_metadata`
  keeps abstract text if a caller supplies it. The "never kept" rule is
  enforced by the PubMed mapper always passing `None` and by later layers
  having no abstract field.
- **A year-filtered search can return a record with a different citation
  year**, because PubMed's `[pdat]` index differs from `PubDate/Year`.
  Documented in `LEARNING_NOTES.md`.
- **`PubMedHttpError.__cause__` holds the original `HTTPError`,** whose `.url`
  attribute contains the request URL including the API key. Messages,
  tracebacks, and logs do not print it; only code that inspects the chained
  exception could read it.
- **DOI rules are defined three times** (`pubmed.py`, `pipeline.py`,
  `storage.py`), and `storage.py` imports the private helper
  `_strip_doi_prefix` from `pipeline.py`.
- **Naming mixes British and American spelling** (`normalise` versus
  `_normalize_` in `storage.py`). Noted in `CLAUDE.md` as a known exception.

## 4. Code health

| Check | Result |
| --- | --- |
| Dependency file exists and is pinned | **Was missing.** Added `requirements.txt`; the project has no third-party dependencies, so there is nothing to pin yet. |
| A unittest command works from a clean clone | **Yes**, with the documented command. Plain `python3 -m unittest` reports "Ran 0 tests", and `python` does not exist on this machine. |
| README setup steps work | **There were none** beyond the test command. Added a Setup section; verified from a fresh clone. |
| README project structure matches a clone | **It did not:** `src/retrieval`, `src/generation`, `src/evaluation`, `data/raw`, and `data/processed` were empty folders, which Git does not track. Added `.gitkeep` files. |
| Local data protected from accidental commits | **It was not:** nothing ignored `data/` contents or SQLite files. Added ignore rules. |
| Secrets | None in code, tests, docs, or Git history (scanned for keys and tokens); `.env` is ignored. |
| Working tree | Clean before the audit; no stray databases or caches tracked. |

### Fixed in this phase (small setup issues only)

1. Added `requirements.txt` stating there are no third-party dependencies.
2. Added `.env.example` listing every `PUBMED_*` variable, with no real values.
3. Added `.gitkeep` files so the folders in the README exist in a clone.
4. Added `.gitignore` rules for `data/raw/*`, `data/processed/*`, `*.sqlite3`,
   and `*.db`.
5. Added a Setup section to `README.md`.

No source or test file was changed. The suite still reports 118 tests, OK.

## 4a. Known unchecked areas (recorded 2026-11-16)

These have not been verified and are accepted for now:

| Area | Status |
| --- | --- |
| Windows | Never run. Deferred to continuous integration in Phase 6. |
| Very large databases | Local search ranks every candidate row in memory (see section 3). Not measured beyond test-sized data. |
| Two `hri` commands writing at once | Addressed by SQLite WAL mode and a 10-second busy timeout; covered by an eight-thread writer test, but not by separate processes. |
| Live PubMed | Tests never contact PubMed. Manual live checks are recorded in `PROJECT_LOG.md` when they are run. |
| PubMed client with an API key, or under sustained load | Not exercised. |
| Upgrading a real, long-lived database | Upgrades are tested against databases built in each earlier layout, not against one in real use. |

## 5. Proposals (not implemented; each needs approval)

| # | Proposal | Addresses | Size |
| --- | --- | --- | --- |
| P1 | **Done 2026-11-16** (counted, not mapped). Count `<PubmedBookArticle>` records in `invalid_record_count` (or map them as a new record type) and stop raising when a page holds only books. | B1 | Small to count; medium to map |
| P2 | **Done 2026-11-16.** Add tests asserting `PUBMED_ATTRIBUTION` on `PubMedSearchPage` and `SearchResult`. | Untested attribution | Small |
| P3 | **Done 2026-11-16** (source recorded per paper). Carry attribution with stored results: a source field on `StoredPaper`, or a documented wrapper returned by `PaperStore` reads. Needs a schema decision. | Missing attribution | Medium |
| P4 | Decide whether authorless records are valid; if so, allow an empty author tuple. Changes the `PaperMetadata` contract. | B2 | Medium |
| P5 | Move DOI/PMID normalisation into one shared module used by the client, pipeline, and storage; accept DOI prefixes in `lookup_doi`; strip leading zeros from PMIDs. | B3, B4, duplication | Medium |
| P6 | Add tests for the low-risk untested validation paths listed in section 2. | Coverage | Small |
| P7 | **Done 2026-11-16** (Phase 1). Add `pyproject.toml` with a declared minimum Python version so the package installs and tests run without `PYTHONPATH`. | Packaging | Small |
| P8 | Add a CI workflow running the suite on Python 3.9 and the latest version. | No CI | Small |
| P9 | Choose and add a licence. | No licence | Decision |
| P10 | Replace in-memory ranking with SQLite FTS5 when the stored set grows. | Search scale | Large; not needed yet |
| P11 | Raise `PubMedHttpError` without chaining the original `HTTPError` (`from None`) so the request URL cannot be reached through the exception. | Secret hygiene | Small |
