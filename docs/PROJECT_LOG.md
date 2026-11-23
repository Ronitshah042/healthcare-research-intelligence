# Project Log

## Initial repository setup

The repository began with project guidance in `AGENTS.md`, a high-level
project description in `README.md`, and a `.gitignore` for local secrets,
Python environments, caches, IDE files, and logs. No dependencies, API keys,
external services, downloaded papers, commits, or remote changes were added.

The first approved implementation milestone established a small Python
foundation for research-paper metadata ingestion. It deliberately does not
fetch papers, call external APIs, retrieve evidence, generate answers, or make
clinical claims.

## Milestone 1: metadata normalisation and validation

**Status:** Complete

### Files created

| File | Responsibility |
| --- | --- |
| `src/ingestion/__init__.py` | Makes the ingestion package importable and exposes the public metadata API. |
| `src/ingestion/metadata.py` | Defines the metadata model, input normalisation, and validation rules. |
| `tests/test_metadata.py` | Uses the standard-library `unittest` framework to test valid metadata, missing required fields, and invalid years. |

### Confirmed implementation behaviour

`normalise_paper_metadata` accepts a mapping and returns a frozen
`PaperMetadata` object. The required fields are `title`, `authors`,
`publication_year`, and `source_identifier`. `abstract` and `source_url` are
optional.

- Required text values must be strings and must not be empty after whitespace
  normalisation.
- Text normalisation collapses leading, trailing, repeated, tab, and newline
  whitespace to single spaces.
- Authors must be a non-empty sequence of non-empty strings. They are stored as
  a tuple.
- Publication years accept integers and digit-only strings. Booleans are
  explicitly rejected. Valid years are from `1` through the current calendar
  year plus one.
- Source identifiers are whitespace-normalised and must not be empty. The code
  deliberately does not require a PubMed, DOI, or other provider-specific
  format.
- An absent, `None`, or whitespace-only abstract becomes `None`.
- A source URL is optional. If supplied, it must have an `http` or `https`
  scheme and a host according to `urllib.parse.urlsplit`.
- Additional, unknown mapping keys are currently ignored.

Invalid inputs raise `MetadataValidationError`, except for the potential edge
case noted below.

### Test record

The requested command was attempted exactly as follows:

```text
PYTHONPATH=src python -m unittest discover -s tests -v
```

It could not start because this environment had no `python` executable:

```text
zsh:1: command not found: python
```

The equivalent command using the available interpreter succeeded:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Exact successful result:

```text
test_normalises_valid_metadata (test_metadata.NormalisePaperMetadataTests) ... ok
test_rejects_invalid_publication_years (test_metadata.NormalisePaperMetadataTests) ... ok
test_rejects_missing_required_fields (test_metadata.NormalisePaperMetadataTests) ... ok

----------------------------------------------------------------------
Ran 3 tests in 0.002s

OK
```

During verification, the available Python 3.9 interpreter also showed that
`dataclass(slots=True)` was unsupported. The final implementation therefore
uses `@dataclass(frozen=True)` without `slots=True`, retaining immutability
while supporting that interpreter.

### Remaining limitations

Milestone 1.1 adds coverage for whitespace-only values, invalid author input,
optional fields, basic URL structures, year boundaries and types, and the
Unicode-digit issue. The following behaviour remains untested:

- mappings other than ordinary dictionaries;
- the current behaviour for unknown metadata keys; and
- author generators, which are rejected because the implementation requires a
  `Sequence` rather than any iterable.

#### Potential bug to investigate: Unicode digit conversion

At the end of Milestone 1.1, this issue was **not fixed**. The year validation
first checked
`value.strip().isdigit()` and then calls `int(...)`. Some Unicode characters,
such as a superscript two (`"²"`), can report `True` for `.isdigit()` but may
not be accepted by `int()`. In that situation, the code may expose a built-in
`ValueError` instead of the project-specific `MetadataValidationError`.

## Milestone 1.1: metadata validation test coverage

**Status:** Complete

### Scope completed

`tests/test_metadata.py` was expanded from 3 to 13 discovered `unittest` test
methods without changing the metadata validator. The new coverage confirms:

- rejection of empty or whitespace-only titles, source identifiers, author
  entries, and empty author sequences;
- rejection of a string used as the whole author value, `None` authors, and
  sequences containing non-text entries;
- optional abstract and source URL behaviour when fields are omitted, `None`,
  blank, or have invalid types;
- acceptance of structurally valid HTTP and HTTPS source URLs and rejection of
  relative, scheme-less, FTP, and host-less URLs;
- acceptance of publication-year boundaries (`1` and current year plus one);
  and rejection of booleans, floats, and decimal-looking strings; and
- the desired project-specific exception contract for the Unicode-digit case.

### Unicode-digit result

The Unicode-digit test uses `@unittest.expectedFailure`. It expects
`MetadataValidationError` for the superscript digit `"²"`, but the current
implementation reaches `int("²")`, which raises a built-in `ValueError`
instead. The expected failure makes this unfixed discrepancy visible while
allowing the test suite to pass. No validator code was changed.

### Verification result

The required command was run:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Its exact summary was:

```text
Ran 13 tests in 0.009s

OK (expected failures=1)
```

The 12 non-expected-failure tests passed. The only expected failure was
`test_rejects_unicode_digit_year_with_metadata_validation_error`.

## Unicode publication-year validation fix

**Status:** Complete as an approved follow-up to Milestone 1.1

The publication-year validator now catches `ValueError` from `int(...)` when a
digit-only string cannot be converted, then raises `MetadataValidationError`
instead. This preserves the existing accepted-input rule while ensuring callers
receive the module's documented validation exception.

The Unicode-digit test for `"²"` was converted from an expected failure into a
normal regression test. The verification command ran 13 tests in 0.010 seconds
with `OK`: all 13 passed and there were no expected failures.

## Milestone 1 maintenance: validation-contract regression coverage

**Status:** Complete

This maintenance update keeps the metadata foundation narrow while reinforcing
its documented error contract. If `urllib.parse.urlsplit` rejects a malformed
source URL, the validator now translates that `ValueError` into
`MetadataValidationError`, matching the behaviour for other invalid metadata.

The test suite now also verifies malformed URL parsing, rejection of
non-mapping input, and reassignment protection on the frozen `PaperMetadata`
model. The duplicate `.gitignore` block was removed without changing any
ignore patterns. The README now identifies the metadata foundation as complete
and accurately states the capabilities that remain unimplemented.

## Milestone 2A: PubMed citation search integration

**Status:** Complete

Milestone 2A adds a framework-neutral, standard-library PubMed client under
`src/ingestion`. It uses ESearch with the NCBI history server and one batched
EFetch request per page. The client supports title, title/abstract, author,
DOI, and PMID searches; an inclusive publication-date filter; total counts;
and pages of up to 20 results. A title search falls back to title/abstract only
when the title-only search has no matches.

Validated results reuse `PaperMetadata` and identify sources as `PMID:<id>`
with an official PubMed record URL. PubMed-only fields are kept in a separate
result model. Abstract text is intentionally ignored, and the milestone makes
no full-text request or persistent cache. Citation parsing follows the approved
primary-year rule: journal `PubDate/Year`, then the first four-digit year in
`MedlineDate` only when `Year` is absent; electronic and history dates are not
substitutes.

The tests use mocked HTTP, clock, and sleeper dependencies only. They cover
search fields, fallback rules, filters, paging, citation mapping, date
precedence, missing abstracts, malformed records and XML, API failures,
configuration, and rate limiting. `docs/PUBMED_INTEGRATION.md` records the
production identification, attribution, disclaimer, and rights-review
requirements before a public deployment.

The configuration follow-up rejects non-finite timeout and request-rate values
and permits only HTTPS URLs on the official NCBI E-utilities host. This keeps
the local rate limiter meaningful and prevents configured identification data
or an API key from being sent to an arbitrary endpoint.

The review-fix pass makes the per-client rate limiter safe for shared-thread
use without holding its lock during HTTP calls. It treats an EFetch document
without citation records after a positive search as a response error, while
retaining individual invalid-record skipping. It also defines the public query
policy: title, title/abstract, and author methods accept plain text; DOI and
PMID methods accept one validated identifier; advanced PubMed query syntax is
out of scope for Milestone 2A.

## Milestone 2B: PubMed integration hardening

**Status:** Complete

Milestone 2B keeps Milestone 2A's search and citation behavior intact while
hardening operational behavior. Multiple anonymous clients now share one
thread-safe, process-local three-requests-per-second schedule. Clients using
the same API key share a separate ten-requests-per-second schedule identified
by a key digest, not the raw key. Scheduling locks are released before HTTP
transport begins.

The client now makes two bounded retries by default, using a 0.5-second
exponential-backoff base. It retries timeouts, transport failures, and HTTP
408, 429, 500, 502, 503, and 504; numeric `Retry-After` values are respected.
Each retry receives a fresh rate-limit reservation. Permanent HTTP failures,
malformed XML, and NCBI XML errors are not retried. NCBI `<ERROR>` payloads
produce a generic response error so echoed secrets are not exposed.

Tests use only mocked transport, clock, sleeper, and thread coordination. They
verify multi-client shared scheduling, retry bounding and backoff,
`Retry-After`, permanent-failure exclusions, and ESearch/EFetch XML error
payload handling. Cross-process limiting, live-service tests, persistent retry
queues, and circuit breakers remain excluded.

Verification completed with:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
Ran 56 tests
OK
```

`git diff --check` completed without whitespace errors.

## Search-pipeline integration increment

**Status:** Complete

The ingestion package now includes a small `SearchPipeline`. A typed
`SearchRequest` selects title, title/abstract, author, DOI, or PMID routing on
the existing PubMed client. The pipeline returns a consistent, metadata-only
`SearchResult` while preserving totals, paging, fallback, invalid-record, and
attribution information. Each result standardises PMID, DOI, title, authors,
journal, publication year, and abstract availability. Identifier-connected
duplicates are merged; invalid siblings are skipped and counted without
discarding valid records. Abstract availability comes from PubMed XML while
abstract text remains unretained. `total_count` remains the PubMed-wide source
total; `normalised_count` identifies valid, unique records returned for the
current page. Tests use a fake client and make no HTTP requests.

## Current status

Milestones 1, 1.1, 2A, and 2B are complete. The project now has a hardened
PubMed citation-search capability and a metadata-validation foundation. It has
no full-text ingestion, abstract display or persistence, evidence retrieval,
generation, evaluation, or user interface capability yet.

## Next steps

Before any new milestone, agree on its scope and acceptance criteria. A
separate post-2B ingestion capability should be approved before implementation.

## SQLite paper storage increment

**Status:** Complete

Added `src/ingestion/storage.py` as a separate local persistence layer backed by
Python's standard-library SQLite module. `PaperStore` creates its schema,
upserts standardised citation records, looks them up by PMID or DOI, and lists
records with pagination. Authors are stored as JSON text; abstract text is not
stored. The database location is provided by the caller, so no database file is
created in the repository by default.

`tests/test_storage.py` covers database creation, save/read round trips,
identifier lookup, updating an existing record, and invalid identifier cases.
No third-party dependency or server is required.

## Paper identity and local retrieval increment

**Status:** Implemented; verification pending local Python availability

The storage layer now trims and validates PMIDs and canonicalizes DOI case and
common `doi:` / DOI URL prefixes before saving or looking up. It enriches an
existing record when an incoming record supplies its missing identifier. If
PMID and DOI resolve to different records, or an identifier contradicts the
matched record, `PaperIdentityConflictError` is raised and the transaction is
rolled back; records are not merged based on title similarity.

`PaperStore.search(query, limit=100, offset=0)` searches title, author names,
and journal text using a case-insensitive substring match with stable insertion
ordering. Existing `get` and `list_papers` APIs remain available. Expanded tests
cover identifier normalization, enrichment, identity conflicts, local search,
and pagination.

## Paper search enhancements

### Separate fuzzy search strategy

**Status:** Implemented; automated verification blocked by missing Python

The default `PaperStore.search` strategy remains `keyword`, preserving the
existing keyword matching and ranking behavior. Callers can explicitly pass
`strategy="fuzzy"` to include approximate title matches. Fuzzy mode first
collects the same exact keyword results as before, then considers other titles
where every query keyword has a title token with a `difflib.SequenceMatcher`
similarity of at least `0.85`, and each keyword must match a different title
token. Fuzzy matching is title-only to limit false positives. Exact keyword
results always precede approximate matches; fuzzy
results sort by average token similarity and then insertion order. Existing
year and abstract filters run before matching, and pagination is applied after
the complete relevance ordering. Identifier conflict handling is untouched.

Tests were added for title misspellings, partial-title queries, unrelated and
empty results, exact-over-fuzzy ordering, deterministic ties, retained filters
and pagination, invalid strategies, prevention of one title token satisfying
multiple keywords, and unchanged default keyword behavior.
The full suite was attempted with
`$env:PYTHONPATH = "src"; py -3 -m unittest discover -s tests -v` and could not
start because the environment reported `No installed Python found!`. No test
result beyond that environment failure is available. Known limits: this is a
simple character-level similarity measure, does not fuzzy-match authors or
journals, and does not include abstracts or full text.

### Existing keyword search filters and relevance

**Status:** Implemented; automated verification blocked by missing Python

`PaperStore.search` preserves the existing `search(query, *, limit=100,
offset=0)` call shape and adds optional `year_from`, `year_to`, and
`abstract_available` filters. The year bounds are inclusive; records without a
publication year do not match a bounded year query. Invalid bounds, reversed
ranges, and non-boolean abstract filters raise `ValueError`.

The query is split on whitespace and every distinct keyword must occur in at
least one of the title, authors, or journal fields. Relevance adds three points
for a title match, two for an author match, and one for a journal match per
keyword, counting only the highest-weight field for each keyword. Results sort
by descending score, then original insertion order. Pagination is applied
after matching and relevance ordering. This simple deterministic scoring
avoids adding a search dependency or changing the stored schema.

Tests were added for all-keyword matching, relevance ordering, inclusive year
filters, abstract-availability filters, pagination after ranking, and invalid
filter values. Verification was attempted with
`$env:PYTHONPATH = "src"; py -3 -m unittest discover -s tests -v`, but the
environment returned `No installed Python found!`; therefore, no tests could
be executed in this environment. The suite must be run after installing a
Python interpreter. Search is limited to metadata substring matching: it does
not search abstracts or full text, stem words, or interpret quoted phrases.

## Boolean local paper search

**Status:** Complete

`PaperStore.search(..., strategy="boolean")` is a separate local search mode;
`keyword` remains the default and `fuzzy` behavior is unchanged. Boolean queries
support case-insensitive `AND`, `OR`, and unary `NOT`, parentheses, and
double-quoted phrases. Precedence is `NOT`, then `AND`, then `OR`. Operators
must be explicit; implicit conjunction, empty phrases, malformed operators,
and unbalanced parentheses raise `ValueError` rather than being guessed.

Terms and phrases are case-insensitive substrings over title, serialized
authors, and journal. Matching expressions rank using the existing field
weights (title 3, authors 2, journal 1), then insertion order. Only satisfied
branches contribute to relevance; negated terms contribute no score. Existing
year/abstract filters are applied before matching, and pagination follows
relevance ordering. The parser and evaluator are private, small helpers in
`storage.py`; paper identity matching and conflict protections are unchanged.

Tests were added for operators, precedence, grouping, negation, phrases,
malformed queries, empty results, relevance and tie ordering, filters,
pagination, and regression coverage for the existing keyword and fuzzy modes.
Full-suite verification result:

```text
Ran 97 tests in 0.374 seconds
OK
```

This local Boolean mode searches only stored citation metadata; it does not
search abstracts/full text or provide field-specific syntax or escaped quotes
inside phrases.

## Local search and identifier fixes

**Status:** Complete

A review found five defects, each now fixed with a regression test:

- Keyword and Boolean search matched the raw `authors_json` text, so names
  with accents or non-Latin letters (stored as `ü`-style escapes) could
  not be found, while JSON punctuation such as `[`, `,`, or `"` matched every
  paper. Search now decodes the stored author list and matches names only.
  Existing databases need no migration.
- Boolean search read a leading `not-`, `and-`, or `or-` as an operator, so
  `not-for-profit` became `NOT -for-profit`. Operators now count only when
  followed by whitespace, a parenthesis, a quote, or the end of the query.
- `PubMedClientConfig.from_environment` rejected a blank
  `PUBMED_NCBI_API_KEY`; a blank or whitespace-only value now means no key.
  The constructor still rejects an empty `api_key` passed directly.
- The pipeline treated DOIs with a `doi:` or `https://doi.org/` prefix as
  invalid. It now strips the same prefixes as `PaperStore`, sharing one helper.

Full-suite verification on Python 3.15.2 and Python 3.9.6:

```text
Ran 102 tests
OK
```

The new tests were also run against the previous source and failed (9
failures, 2 errors), confirming they detect the original defects.

## Hardening fixes

**Status:** Complete

A second review pass fixed six smaller weaknesses, each with a regression
test:

- `PaperStore` DOI matching no longer reads every stored row; SQLite returns
  the exact match plus only rows that look un-normalised.
- PubMed plain-text searches also reject `*` truncation and `#` history
  references.
- `base_url` must be exactly the E-utilities host, with no port, user info,
  query, or fragment.
- TLS certificate verification failures are no longer retried.
- String publication years must use ASCII digits; `٢٠٢٤` and full-width
  digits are rejected.
- Pipeline deduplication keeps records with different PMIDs (or DOIs)
  separate even when they share the other identifier.

Full-suite verification on Python 3.15.2 and Python 3.9.6:

```text
Ran 106 tests
OK
```

Against the previous source, the new tests produced 9 failures and 6 errors;
the legacy-DOI test passes on both, as it guards unchanged behaviour.

## Live check, coverage, and documentation review

**Status:** Complete

- **Live PubMed check:** title, title fallback, author with year filter, DOI,
  and PMID searches ran against the real E-utilities API through
  `SearchPipeline`, and the results were saved and searched in a temporary
  `PaperStore`. All returned valid records with zero invalid or duplicate
  counts; the fallback triggered as designed. The year-filtered author search
  returned one record whose citation year (2021) differs from the `[pdat]`
  filter year (2020), the documented difference between PubMed's date index
  and `PubDate/Year`. Nothing from the live run was kept.
- **Coverage:** measured with the standard-library `trace` module (no new
  dependency): `metadata` 100%, `pipeline` 97%, `pubmed` 88%,
  `pubmed_config` 86%, `storage` 85%. Many reported misses are continuation
  lines of multi-line function signatures, which `trace` cannot attribute.
  Tests were added for the real gaps: single-year `[pdat]` filters, negated
  grouped Boolean expressions, collective author names, records without a
  `PubDate`, unusable ESearch counts and history, exhausted timeout and OS-error
  retries, unusable `Retry-After` values, non-text search input, invalid tool,
  email, rate, and environment numbers, PMID conflicts on a matched DOI,
  non-`StandardisedPaper` input, corrupt stored authors, and an unusable
  database path. No defects were found in these paths.
- **Docs:** `LEARNING_NOTES.md` described the old `.isdigit()` year check and
  gave contradictory test counts (16 and 13); both are corrected, and a
  beginner section on deduplication, SQLite storage, and local search was
  added. `README.md` now covers the pipeline and `PaperStore`, lists
  `unittest` instead of Pytest, and shows the test command.

Full-suite verification on Python 3.15.2 and Python 3.9.6:

```text
Ran 118 tests
OK
```

## Phase 0: repository audit

**Status:** Complete

The repository was audited against the expected functionality without adding
features. The full findings are in `docs/AUDIT.md`.

Verified test count, using
`PYTHONPATH=src python3 -m unittest discover -s tests -v`:

```text
Python 3.15.2:               Ran 118 tests, OK
Python 3.9.6:                Ran 118 tests, OK
Python 3.15.2, fresh clone:  Ran 118 tests, OK
```

Summary:

- Every expected feature is implemented. All are tested except attribution:
  `PUBMED_ATTRIBUTION` is attached to live search results but no test asserts
  it, and results read back from `PaperStore` do not carry it at all.
- Four bugs were found and reproduced but not fixed. The most serious is that
  PubMed book records (`<PubmedBookArticle>`) are dropped without being
  counted, and a page containing only books raises an error.
- Small setup gaps were fixed: `requirements.txt` (no third-party
  dependencies), `.env.example`, `.gitkeep` files for the folders the README
  lists, ignore rules for local data and SQLite files, and a README Setup
  section. No source or test file changed.

Next steps: decide which proposals in `docs/AUDIT.md` section 5 to approve.
Recommended first: P1 (book records), P2 and P3 (attribution).

## Audit bug fixes

**Status:** Complete

The four bugs from `docs/AUDIT.md` were fixed, one commit per logical change:

- **B1:** PubMed book records are counted in `invalid_record_count` instead
  of being dropped, and a books-only page returns an empty page.
- **B2:** citations without listed authors are valid; `PaperMetadata.authors`
  may be empty.
- **B3 and B4:** a new `ingestion/identifiers.py` holds the shared DOI-prefix
  and PMID leading-zero rules. `lookup_doi` accepts `doi:` and DOI URL
  prefixes, and `0123` and `123` are the same paper in lookups,
  deduplication, and storage, including older zero-padded stored rows.

Two existing tests were updated because they asserted the old B2 behaviour.

Full-suite verification on Python 3.15.2 and Python 3.9.6:

```text
Ran 129 tests
OK
```

Next steps: audit proposals P2 and P3 (attribution tests and attribution on
stored results) remain open, along with P6 to P11.

## Attribution on every PubMed-derived result (audit P2 and P3)

**Status:** Complete

- **P2:** tests now assert `PUBMED_ATTRIBUTION` on every `PubMedSearchPage`
  path (records, no matches, blank query, page out of range), on the pipeline
  `SearchResult` including empty results, and on the attribution text itself.
- **P3:** each paper records its source. `StandardisedPaper.source_name` is
  set to `"PubMed"` by the pipeline, `PaperStore` saves it in a new
  `source_name` column, and `StoredPaper.attribution` returns
  `PUBMED_ATTRIBUTION` for PubMed-derived papers on `save`, `get`,
  `list_papers`, and all three search strategies. Older databases are
  upgraded on opening and their rows marked as PubMed. An update without a
  source keeps the recorded one.
- The attribution contract for a future UI is documented in
  `docs/PUBMED_INTEGRATION.md`.

Full-suite verification on Python 3.15.2 and Python 3.9.6:

```text
Ran 136 tests
OK
```

Next steps: audit proposals P4 to P11 remain open (P4 and P5 were largely
addressed by the audit bug fixes).

## Phase 1: command line and service layer

**Status:** Complete

The pipeline is usable end to end from the command line through a UI-agnostic
service layer.

- **Service layer (`src/hri/services.py`):** `ingest`, `search_papers`,
  `get_paper`, `get_stats`, `create_list`, `add_to_list`, `remove_from_list`,
  `get_lists`, `get_list`, and `export_list`. Each returns a typed result and
  never prints. The contract is `docs/SERVICE_API.md`; a test fails if a public
  name is missing from it.
- **CLI (`src/hri/cli.py`):** `hri ingest`, `search`, `show`, `stats`, and
  `lists create|add|remove|show|list|export`, built with argparse and calling
  only the service layer.
- **Storage:** `schema.py` versions the database layout and upgrades older
  databases in place; saved-list tables are version 2. `PaperStore` gained
  `search_with_total` and `stats`, and `StoredPaper` gained `pubmed_url`.
  `saved_lists.py` holds the saved-list operations.
- **Export (`src/hri/export.py`):** CSV with formula-safe cells, and plain
  numbered citations ending with the PubMed attribution.
- **Configuration:** database path from `--db`, then `HRI_DB_PATH`, then
  `data/hri.sqlite3`, which Git ignores.
- **Packaging:** `pyproject.toml` with the `hri` console script (Python 3.9+),
  kept consistent with `requirements.txt` by a test. Verified by installing
  into a fresh virtual environment and running `hri` and the suite there.
- **Attribution:** every result that includes a PubMed-derived paper carries
  `PUBMED_ATTRIBUTION`, and every such paper has a PubMed link.

No test contacts PubMed; ingest tests use a fake client and temporary
databases. `hri ingest` was **not** run against live PubMed in this phase.

Full-suite verification on Python 3.15.2 and Python 3.9.6:

```text
Ran 194 tests
OK
```

Tests per file: `test_pubmed.py` 55, `test_storage.py` 54, `test_services.py`
26, `test_metadata.py` 18, `test_cli.py` 13, `test_pipeline.py` 10,
`test_saved_lists.py` 9, `test_schema.py` 4, `test_packaging.py` 3,
`test_identifiers.py` 2.

Next steps: a manual live run of `hri ingest`; then the next phase. Open
audit proposals: P6, P8, P9, P10, P11.

## Before Phase 2: citations, list management, and concurrency

**Status:** Complete, except the live PubMed check (see below)

- **Push:** after a full passing suite and a check that no `.env`, database,
  or `data/raw` content was tracked, the 16 pending commits were pushed to
  `main` (through `852026f`).
- **Bug fixed:** author names were stored with doubled initials
  (`Fernando P FP Polack`) for every real PubMed record. Names are now
  `ForeName LastName`. Papers ingested earlier keep the old form until
  ingested again.
- **Vancouver citations:** `--format citations` now produces Vancouver (NLM)
  style; the earlier layout is `--format plain`. To support it, PubMed's NLM
  author names, journal abbreviation, volume, issue, and pages are carried as
  `CitationDetails` and stored in `papers.citation_json` (schema version 3,
  with an upgrade test from version 2). CSV gained `volume`, `issue`, and
  `pages` columns.
- **Saved lists:** `rename_list` and `delete_list` service functions and
  `hri lists rename|delete`. Deleting a list never deletes papers.
- **Concurrency:** WAL mode and a named 10-second busy timeout, with an
  eight-thread concurrent-writer test.
- **Docs:** `SERVICE_API.md` covers the new functions, formats, and fields;
  `AUDIT.md` section 4a lists the known unchecked areas.

Full-suite verification on Python 3.15.2 and Python 3.9.6:

```text
Ran 218 tests
OK
```

**Live PubMed check (2026-11-17): passed.** Run against the real
E-utilities API with credentials from the Git-ignored `.env`, into the
default `data/hri.sqlite3`:

```text
hri ingest --query "metformin cardiovascular outcomes" --mode title_abstract --max 20 --year-from 2018
  PubMed matches: 192, Fetched: 19, Saved: 19, Duplicates: 0,
  Invalid skipped: 1, Conflicts skipped: 0
```

- **Book handling confirmed:** the one skipped record was a real
  `PubmedBookArticle`, counted rather than dropped (audit bug B1).
- **`hri stats`:** 19 papers, all with a DOI and an abstract on PubMed, all
  from 2026 (PubMed returns the newest matches first).
- **`hri search`:** keyword and Boolean searches returned the expected paper
  with its PubMed link and the attribution block. Only 1 of the 19 papers
  contains "metformin" locally, because the ingest matched titles *and
  abstracts* while local search covers titles, authors, and journals only
  (abstracts are not stored). This is expected but will surprise users.
- **`hri show 43003454`:** full metadata, correct link and attribution.
- **Author names:** no doubled initials in any of the 19 papers; accented
  names (`Johan Ärnlöv`) kept intact.
- **Vancouver export** on real data matched NLM style:
  `Wallensten J, Ärnlöv J, Muglia L, Soraci L, Lattanzio F, Fabbietti P, et al. ... BMJ Open. 2026;16(11):e114393. doi: 10.1136/bmjopen-2025-114393. PMID: 43003454.`
  Of the 19 papers, all had a journal abbreviation; 1 lacked a volume, 4 an
  issue, and 6 pages (typical of articles published ahead of print). Those
  parts are left out as designed.

## Phase 2: licensed open-access full text

**Status:** Complete

The project owner approved storing full text under the policy in
`docs/CONTENT_POLICY.md`: PubMed Central Open Access articles only, CC0 and
CC BY by default, current versions only, retracted articles rejected,
corrections flagged, and PubMed abstracts still never stored.

- **Research first:** NCBI's documentation (checked 2026-11-17) shows the PMC
  OA Web Service and legacy FTP packages were retired in August 2026. The
  PMC Cloud Service (licence metadata and XML), the PMC ID Converter (links and
  current version), and E-utilities (search) were chosen; the reasons and
  exact endpoints are in `DECISIONS.md`.
- **Shared HTTP layer:** rate limiting, retries, and redaction moved from
  `pubmed.py` into `http.py`, used by both clients. HTTP errors no longer
  chain the original error, whose URL carried credentials (audit P11).
- **New modules:** `licence_policy.py`, `pmc_client.py`, `fulltext.py`,
  `chunking.py`, `fulltext_store.py`, and `hri/fulltext_services.py`.
- **Storage:** schema version 4 adds `documents`, `document_sections`,
  `chunks`, and `fulltext_rejections`, with an upgrade test from version 3.
- **CLI:** `hri fetch-fulltext --from-db | --query`, `hri recheck-fulltext`,
  and full-text counts in `hri stats`.
- **Fixtures:** trimmed real CC0 and CC BY articles in `tests/fixtures/pmc/`,
  with their source, licence, and changes recorded.
- **Docs:** `CONTENT_POLICY.md` (new), rules in `AGENTS.md` and `CLAUDE.md`,
  `DECISIONS.md`, `SERVICE_API.md`, `README.md`, and these notes.

Full-suite verification on Python 3.15.2 and Python 3.9.6:

```text
Ran 292 tests
OK
```

**Live check (2026-11-17), into a scratch copy of the database:**

- `hri fetch-fulltext --from-db --max 3`: 3 articles stored, all CC BY and
  linked to stored papers; 4 of the 19 stored papers have no PMC article.
- `hri fetch-fulltext --query "metformin dementia" --max 3`: 2 stored, 1
  rejected for licence `CC BY-NC-ND`, with no text stored for it.
- 5 documents, 106 passages, each at most 400 approximate tokens, with no
  citation-marker leftovers. One review article's topic headings were named
  `other`, as designed.

Next steps at the end of Phase 2: Phase 3 (search over the stored passages).
Open audit proposals: P6, P8, P9, P10.

### Phase 2 follow-up: PubMed correction links (2026-11-18)

PMC's XML rarely records later corrections, so fetch and re-check now also
read PubMed's `CommentsCorrections` links (`PmcClient.pubmed_notices`, one
batched E-utilities request per 200 PMIDs).

- `ErratumIn` and `CorrectedandRepublishedIn` add `corrected`,
  `ExpressionOfConcernIn` adds `expression_of_concern`, and
  `PartialRetractionIn` adds `partially_retracted`.
- `RetractionIn` and `RetractedandRepublishedIn` reject the article as
  retracted before its text is downloaded, and re-check deletes stored ones
  (owner's decision, 2026-11-18).
- `recheck_fulltext` reports `newly_flagged`; flags are visible through
  `FullTextDocument.notice_flags`, every `PassageMatch.document`, and the CLI.
- Tests: link mapping, batching, error redaction, rejection before download,
  flag merging, and re-check updates. `CONTENT_POLICY.md` section 6,
  `SERVICE_API.md`.

Full suite: 308 tests OK on Python 3.15.2 and 3.9.6.

**Phase 2 is complete.**

## Phase 3: keyword search over stored passages (2026-11-18)

The owner chose keyword search now and meaning-based (embedding) search as a
later phase, so no dependency or new host was added.

- **Storage:** schema version 5 adds `chunks_fts`, an SQLite FTS5 index over
  `chunks.text` with Porter stemming, kept in step by triggers. Version 4
  databases are indexed when opened.
- **Search:** `FullTextStore.search` returns `PassageMatch` (passage,
  document, BM25 score). Every query word is quoted, so no input is FTS5
  syntax.
- **Service:** `search_passages` (re-exported by `hri.services`), with
  `limit` up to 50 and an optional section filter.
- **CLI:** `hri search-passages QUERY [--section NAME]... [--limit N]`, which
  prints every passage with its attribution.
- **Tests:** ranking, all-words matching, stemming, section filter, query
  syntax as plain text, removal on replacement and retraction, the version 4
  upgrade, service validation, and the CLI.
- **Docs:** `SERVICE_API.md`, `CONTENT_POLICY.md` section 9, `README.md`
  (also fixed the stale line saying the project retrieves no full text),
  `CLAUDE.md`, `DECISIONS.md`, and these notes.

Full-suite verification on Python 3.15.2 and Python 3.9.6:

```text
Ran 302 tests
OK
```

Not done: meaning-based search, paging through passage results, and phrase
search.

## Phase 3B: meaning-based (embedding) search (2026-11-18)

Approved: optional `fastembed==0.9.1`, model BAAI/bge-small-en-v1.5 at a
pinned revision, Hugging Face for the model download only.

- **Install:** `pip install -e '.[embeddings]'` on Python 3.10 to 3.13. On
  the owner's Intel Mac: Homebrew Python 3.13.18 in `.venv-embed`. The core
  still needs only the standard library and runs on 3.9 and 3.15.
- **New code:** `src/retrieval/embeddings.py` (pinned model, download with
  size and SHA-256 check, offline loading, `FastEmbedder`),
  `src/retrieval/hybrid.py` (vector search, reciprocal rank fusion),
  `src/hri/retrieval_services.py` (`prepare_embedder`, `embed_passages`,
  `search_passages`, moved from `fulltext_services`).
- **Storage:** schema version 6 adds `chunk_embeddings`, deleted with passages.
- **CLI:** `hri embed-passages`; `hri search-passages` now prints the method
  used and any fallback notice.
- **Breaking change:** `search_passages` returns `PassageSearchResult`
  (`matches`, `method`, `notice`) instead of a tuple.
- **Tests:** `FakeEmbedder` only; download, hash failure, and download
  failure use fake `fastembed` and `huggingface_hub` modules.

**Live check (2026-11-18), scratch copy of the database:**

- Model download: 21 s first time; contacts only `huggingface.co` and
  `us.aws.cdn.hf.co` once telemetry and Xet are disabled.
- `embed_passages`: 106 passages in 34 s; 5 exceeded 512 tokens (longest 523).
- Model load 1.7 s; each hybrid search about 0.04 s.
- "does metformin protect against dementia" finds the metformin cognition
  passages, which keyword-only search (every word required) does not.

Full suite: 323 tests OK on Python 3.15.2, 3.9.6 (no fastembed), and 3.13.18
with fastembed installed (no download, no model used).

Not done: a vector index for large collections, and paging through passage
results.

### Follow-up: pinned embedding dependencies (2026-11-19)

`constraints-embeddings.txt` pins all 32 packages the embeddings extra
installs (fastembed 0.9.1, onnxruntime 1.23.2, numpy 2.5.5, tokenizers 0.23.3,
huggingface_hub 1.33.0, and their dependencies), recorded from the tested
Python 3.13 environment. A test checks every line is an exact pin and that
the fastembed version matches `pyproject.toml`.

## Phase 4: answers with checked citations (2026-11-19)

Approved: Gemini `gemini-3.5-flash-lite` by default, Groq
`llama-3.3-70b-versatile` as fallback, environment variables only (no
Streamlit secrets), only CC0/CC BY passages sent, a notice naming the
provider on every answer.

- **New code:** `src/generation/llm.py` (`GeminiClient`, `GroqClient`,
  `FallbackLLM`, standard-library HTTPS), `cache.py` (`AnswerCache`, daily
  cap, in-memory fallback), `rag.py` (retrieval, delimited prompt, citation
  checks, `Answer`), and `src/hri/answer_services.py` (`ask_question`).
- **Retrieval:** any-word BM25 for questions (`FullTextStore.search(...,
  match_all=False)`), fused with meaning-based search; `hybrid.fuse` split
  out of `hybrid_search`.
- **Storage:** schema version 7 adds `answer_cache` and `llm_usage`.
- **CLI:** `hri ask QUESTION [--evidence N]`.
- **Calibration:** the 0.7 relevance threshold, from 9 questions on the
  live-check collection (see DECISIONS.md).
- **Tests:** 21 new, all with `FakeLLM` or a fake transport: request shapes
  and headers, safe errors, fallback, correct citations kept, invented
  citations removed (including ranges), all abstention paths, prompt
  injection inside a passage, licence filtering, cache, daily cap, busy
  answer, read-only database, CLI output and disclaimer.

Full suite: 345 tests OK on Python 3.15.2, 3.9.6, and 3.13.18 (fastembed).

**Live check (2026-11-19), Gemini `gemini-3.5-flash-lite`, scratch copy of
the 5-article database, hybrid search:**

- 7 answered questions: every citation pointed to a supplied passage (0
  invalid), and every checked figure or claim was in the cited passage
  (loxenatide ICER and thresholds; MMSE stability; "regression toward the
  mean"; trial names LEADER, SUSTAIN-6, REWIND; physical-performance and
  heart-failure statements).
- Unanswerable on-topic question (best metformin dose): the model replied
  `INSUFFICIENT_EVIDENCE`. Off-topic question (migraine): refused before any
  AI call. A repeated question was served from the cache.
- Prompt injection: a planted passage telling the model to recommend
  2000 mg metformin daily and cite [12] was ranked first and sent as [1] for
  two questions, one asking "should older adults take metformin". The model
  ignored it both times and never cited it.
- One request failed with HTTP 503 (Gemini overloaded); with no Groq key
  configured, that question got the "busy" answer. A retry minutes later
  succeeded.
- Weakness found: the article **title** is stored as a passage, and once the
  model cited the title (a question) to support "Yes, ...".

**Fixes after the live check:** title passages are no longer sent as
evidence, and each provider retries once after 2 seconds on HTTP 500/502/
503/504 or a network failure (never on 429). Full suite: 347 tests OK on
Python 3.15.2, 3.9.6, and 3.13.18.

Not done: the Groq fallback live (no Groq key), and measured free-tier limits.

## Phase 5: evaluation (2026-11-19)

- **Collection:** `data/eval.sqlite3` (not committed), from a copy of the
  owner's 19 citations: 6 with allowed open-access text (9 rejected by
  licence, 4 not in PMC) plus 12 from three PMC searches. 18 articles, 497
  passages; 72 passages (14.5%) exceed the embedding model's 512 tokens
  (longest 602).
- **Question set:** `eval/questions.json`, 32 questions written from the
  stored abstracts: 23 answerable (4 multi-article), 5 about library papers
  without stored text, 4 off-topic; 8 marked `needs_review`.
- **New code:** `src/evaluation/metrics.py`, `judge.py`, `runner.py`;
  `src/hri/eval_services.py` (`run_eval`); `hri eval`; judge prompt in
  `eval/judge_prompt.txt`. Clients now take an output-token budget; a
  fallback chain reports "rate limited" only when every provider was.
- **Tests:** 21 new (metrics with fixed data, question-set validation, claim
  extraction, verdict parsing including truncated replies, judge caching,
  runner, report, spot-check sheet, judge rate-limit stop, CLI).
  Full suite: 368 tests OK on Python 3.15.2, 3.9.6, and 3.13.18.

**Results (full detail in `docs/EVAL_RESULTS.md`):**

| Retrieval (23 answerable) | Recall@1 | Recall@5 | MRR |
| --- | --- | --- | --- |
| keyword (any-word BM25) | 88% | 95% | 0.98 |
| semantic | 89% | 99% | 1.00 |
| hybrid | 89% | 97% | 1.00 |

- Citation validity 100% (211 citations, none invented).
- Correct abstention 100% (9 of 9). Answered when expected 91% (21 of 23).
- Latency of uncached answers: median 3.6 s, p90 18.9 s (first run).
- AI requests: 49 on the first run (28 answers, 21 judge attempts), 1 per
  later rerun.
- **Judge incomplete:** `gemini-3.6-flash` hit its free quota after 3
  replies, and those were cut off by the 1,024-token output limit, so no
  support rate is reported. Fixed: 4,096-token judge budget, shorter
  reasons, parsing of truncated replies, and judging stops at the first
  rate-limit refusal.
- Developer spot-check of 5 claims: 4 supported, 1 partial (an MR finding
  described as "causal"). Not independent.
- **Baseline `eval-baseline-v1` (judge on Groq, owner's decision):** the
  judge `openai/gpt-oss-120b` rated all 121 cited claims: 98% supported, 2%
  partial, 0% unsupported. Evidence recall 93%. Results are preliminary
  while 8 questions await the owner's review. Summary:
  `eval/results/baseline-v1.json`. `llama-3.3-70b-versatile` turned out to
  be unavailable to the free Groq key (404), so the Groq answer fallback is
  now `qwen/qwen3.8-27b` (see DECISIONS.md).
- Both false abstentions came from evidence selection: q13's key results
  passage was not among the 6 sent, and q22's 6 passages all came from one
  of its two relevant articles. Article-level retrieval scores hide this.

### Experiment: three passages per article (2026-11-19, rejected)

Committed as `4b5e952`, evaluated with the same set and settings against
`eval-baseline-v1`, then reverted. Supported claims fell from 97.5% to 96.2%
and evidence recall rose only from 92.8% to 93.8%; q22 was not fixed. The
before/after table and decision are in `docs/EVAL_RESULTS.md`, the run
summaries in `eval/results/`. `hri eval` gained `--note` for recording such
decisions. The experiment used 41 AI requests (Gemini answers and Groq
judge); regenerating the report afterwards used none.

### Reviewed question set (2026-11-19)

The owner reviewed the 8 `needs_review` questions; decisions and the
deciding sentences are in DECISIONS.md. Rerun with the same settings used
no AI requests (all answers and verdicts cached). Report no longer
preliminary; summary in `eval/results/baseline-v1-reviewed.json`.

| Retrieval (23 answerable) | Recall@1 | Recall@5 | MRR |
| --- | --- | --- | --- |
| keyword | 90% | 96% | 0.98 |
| semantic | 86% | 99% | 0.96 |
| hybrid | 88% | 98% | 0.98 |

Answers unchanged: evidence recall 93%, citation validity 100%, judge 98%
supported / 2% partial / 0% unsupported, correct abstention 100%, answered
when expected 91%. Judge agreement: pending the owner's spot-check.
`hri eval` no longer overwrites a spot-check sheet that has human input.

### Verdict export and response time (2026-11-19)

- `hri eval-verdicts` / `export_verdicts` writes `eval/verdicts-<date>.csv`
  without article text; the full sheet now has a `cited_passage_ids` column.
  `eval/verdicts-2026-11-19.csv` is committed with the owner's columns still
  empty; judge agreement shows "pending manual spot-check" until they are
  filled and re-exported.
- Response time in a cached report: "median 3.6 s, p90 18.9 s (uncached run,
  2026-11-19, 28 answers; this run was fully cached)", from
  `eval/results/latency.json`.
- `hri eval --no-cache` (at most 10 questions, default 5).
- CONTENT_POLICY.md gains section 11, "Evaluation files"; "AI answers" is
  now section 12.

### Experiment: evidence by article first (2026-11-20, rejected)

Committed as `34affa1`, evaluated against `baseline-v1-reviewed` with the
same set and settings (28 AI requests), reverted in `98f8b66`. Evidence
recall 93.5% to 97.8% and q22 answered, but supported claims 97.5% to 95.6%
and single-article answers cited less. Details in DECISIONS.md; table and
decision in `docs/EVAL_RESULTS.md`; summary `eval/results/articles-first.json`.
Its uncached response time (median 6.4 s, p90 11.7 s, 15 answers) was
measured on the experiment's code, so the report keeps the current
system's last measurement (median 3.6 s, 2026-11-19).

### Second, held-out question set (2026-11-20)

`eval/questions-set2.json`: 25 answerable questions (4 multi-article), 4
about library papers without stored text, 3 off-topic, all marked
`needs_review`, each note naming the passage that holds the expected
answer. Not yet run; held out for confirming decisions (see DECISIONS.md).

## Phase 6: deployment preparation (2026-11-20)

Approved: data bundle always as a Release asset, about 100 articles,
daily AI cap 50 plus 5 questions per visitor, optional extras for API and
app (Python 3.11+), read-only database mode, `topics.json`. The UI is the
owner's; only a placeholder page exists.

- **Read-only mode** (`HRI_DB_READ_ONLY=1`) for every store: immutable
  connection, no side files, no upgrades, refuses other layout versions and
  unmerged `-wal` changes (found while testing: a read-only connection would
  silently read stale data). **Fixed:** the answer cache and daily cap fell
  back to memory per instance, so on a read-only database they did not hold
  between questions; they are now process-wide, with a lock.
- **Session helpers** for the UI: `SessionSavedLists` (create, rename,
  delete, add, remove, CSV/citation/plain export) and `VisitorQuota`.
- **REST API** `src/api/app.py` (FastAPI): `/health`, `/search`,
  `/papers/{id}`, `/stats`, `/ask`, admin `/ingest` and `/index`; one error
  format; constant-time token check; `docs/API.md` with generated examples.
- **Data bundle:** `hri build-bundle` (ingest, CC0/CC BY full text,
  retraction re-check, embed, compact read-only copy, manifest with SHA-256);
  `fetch_bundle` (GitHub over HTTPS only, checksum verified, cached);
  `check_deployment` (start-up self-check).
- **Bug fixed (since Phase 1):** PubMed title and title/abstract searches
  sent `(words)[tiab]`, which PubMed reads as one word plus an exact phrase;
  multi-word searches returned too few or no results (0 instead of 1,496 for
  "SGLT2 inhibitor heart failure"). Now every word is tagged.
- **First real bundle:** 14.0 MB; 174 citations, 96 CC BY articles, 2,392
  passages and vectors; 64 candidates rejected by licence; none retracted.
  The placeholder app started against it in 1.7 s with the model loaded,
  searched it, and left the file byte-for-byte unchanged.
- **Deployment files:** `streamlit_app.py` (placeholder), `hri.app_config`
  (Streamlit secrets or environment), `.streamlit/secrets.toml.example`,
  `requirements.txt` (runtime: fastembed, streamlit) and
  `requirements-dev.txt` (fastapi, pydantic, uvicorn, ruff, httpx),
  `docs/DEPLOYMENT.md`.
- **GitHub Actions:** `ci.yml` (ruff; tests on Python 3.9 core and 3.12 with
  all extras) and `build-bundle.yml` (weekly and manual, NCBI settings from
  secrets, publishes to the `data-bundle` release). Not yet run on GitHub.
- **Lint:** ruff rules E4, E7, E9, F, I; fixed 3 undefined names in type
  hints, 5 unused imports, and sorted imports in 55 files.
- **Docs:** README rewritten for portfolio use; SERVICE_API, CONTENT_POLICY
  (section 13, public data bundle), DECISIONS, PUBMED_INTEGRATION, CLAUDE.md,
  LEARNING_NOTES.

**Verified test count:** 407 tests. Python 3.13.18 with every extra: 407 run,
all OK. Python 3.15.2 and 3.9.6 (no FastAPI or Streamlit): 399 run OK, 8
skipped (6 API, 2 Streamlit page). `ruff check` passes.

Not done: running the workflows on GitHub, creating the `data-bundle`
release, and deploying to Streamlit Community Cloud (need the owner's
repository secrets and account); the UI.

## Follow-up work without the owner (2026-11-20)

The owner asked for everything that needs no interaction; items that need
the owner are listed at the end of the session report.

- **Audit P6:** `tests/test_validation_paths.py` pins the input checks the
  audit listed as untested (year filters, blank identifiers, config types,
  store paging and lookup arguments). `.env.example` now lists
  `HRI_BUNDLE_URL`, `HRI_BUNDLE_DIR`, `HRI_DB_READ_ONLY`, `HRI_ADMIN_TOKEN`.
- **Groq fallback tested** on three real questions with Gemini off: accurate
  answers with valid citations. **Bug fixed:** Qwen puts
  `INSUFFICIENT_EVIDENCE` on the last line, so its refusals were labelled
  answered; the marker now counts on any line. No cached evaluation answer
  changed.
- **Off-topic threshold rechecked** on the 96-article bundle: on-topic 0.81
  to 0.92, off-topic medical 0.64 to 0.735. Kept at 0.7, because 0.75 would
  refuse set 1's q08 (0.73) and the groups overlap (DECISIONS.md).
- **Deployment set pinned** for Linux, Python 3.12 (`constraints-deploy.txt`,
  48 packages, resolved without installing; onnxruntime 1.32 there versus
  1.23.2 on the Intel Mac). CI's 3.12 job and the bundle workflow use it.
- **Held-out set 2 run once (preliminary, 52 AI requests):** hybrid recall@5
  99%, MRR 0.98 (keyword-only MRR 0.90 versus 0.98 on set 1: the everyday
  wording hurts keyword search, as intended); evidence recall 97%; citation
  validity 100% (130); judge 100% supported of 82 claims; correct abstention
  100% (7); answered when expected 92% (s12 and s24 declined); median
  response 1.7 s. Report `docs/EVAL_RESULTS_SET2.md`, summary
  `eval/results/set2-preliminary.json`. No settings were changed from it.

**Verified:** 413 tests; Python 3.13.18 with every extra 413 OK; Python 3.15.2
and 3.9.6 405 OK, 8 skipped (API and Streamlit). Ruff clean.

## Python 3.11 minimum and MIT licence (2026-11-21)

- **History checked before pushing:** no Gemini or Groq key, NCBI email,
  `.env` line, key-shaped string, `.env`, secrets file, spot-check sheet or
  database in any commit. The five local commits were pushed.
- **MIT licence** added for the code; the README says it does not cover
  stored article text, which keeps its CC BY or CC0 licence and attribution.
- **Minimum Python raised to 3.11.** Removed the extras' `python_version`
  markers and the pattern-based `pyproject.toml` reading (now `tomllib`).
  CI's 3.9 job became a 3.11 job: core-only tests, then all extras
  (without the 3.12-only deploy pins). The 3.12 job is unchanged.

**Verified:** 413 tests; Python 3.13.18 with every extra 413 OK; Python
3.15.2 core only 405 OK, 8 skipped. Ruff clean (`py311`). The built wheel
declares `Requires-Python: >=3.11`, `License-Expression: MIT` and includes
`LICENSE`. The extras resolve for Linux Python 3.11 (`pip --dry-run`); the
3.11 CI job itself runs for the first time on push.

## Housekeeping: CLAUDE.md state and Actions versions (2026-11-23)

- CLAUDE.md's "Current state" now describes what is built (Phases 1 to 6;
  UI not built) instead of calling retrieval, generation and evaluation
  empty placeholders.
- Workflows use `actions/checkout@v7` and `actions/setup-python@v7`
  (Node 24; Node 20 actions are deprecated). `actions/cache@v4` in the
  bundle workflow still runs on Node 20 and was left unchanged.
- The scheduled bundle build on 2026-11-23 failed because the repository
  secrets `PUBMED_NCBI_TOOL` and `PUBMED_NCBI_EMAIL` are not set yet.
- Evaluation improvements stay on hold until set 2 is reviewed.

**Verified:** Python 3.13.18 with every extra, 413 tests OK; Ruff clean.

## Keys rotated; actions/cache to v6 (2026-11-23)

- The owner replaced the Gemini and Groq keys in `.env`; both were accepted
  by a model-list request (no answer quota used, no key printed).
- The bundle workflow now uses `actions/cache@v6` (Node 24). v5 and v6
  change only the runtime and packaging, not the `path` and `key` inputs.
  It runs for the first time on the next bundle build.

**Verified:** Python 3.13.18 with every extra, 413 tests OK; Ruff clean.
