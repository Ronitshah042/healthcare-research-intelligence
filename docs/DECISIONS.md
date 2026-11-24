# Technical Decisions

This record distinguishes decisions already implemented from choices that
remain open. It documents Milestones 1 and 2A.

## Confirmed decisions

### Use the Python standard library for the initial metadata foundation

**Decision:** The implementation uses `dataclasses`, `datetime`,
`collections.abc`, `urllib.parse`, and `unittest`, with no third-party
dependencies.

**Reasoning:** The milestone is intentionally small and local. Standard-library
tools are sufficient for data modelling, validation, URL structure checks, and
unit testing, so dependency installation is unnecessary.

### Keep ingestion metadata separate from future retrieval and generation work

**Decision:** The current code is under `src/ingestion` and has no retrieval,
generation, interface, or API integration.

**Reasoning:** Metadata cleaning is an ingestion concern. Keeping it small and
separate makes it easier to test and avoids committing to later architecture
too early.

### Use a frozen `PaperMetadata` dataclass

**Decision:** Validated records are represented by
`@dataclass(frozen=True) PaperMetadata`.

**Reasoning:** Normalisation produces a trusted record. Preventing reassignment
of fields helps avoid accidental mutation after validation.

**Compatibility note:** `slots=True` is not used because the available Python
3.9 interpreter rejected that dataclass option during verification.

### Require basic provenance and bibliographic fields

**Decision:** `title`, `authors`, `publication_year`, and
`source_identifier` are required. `abstract` and `source_url` are optional.

**Reasoning:** The required fields provide a basic identity for a paper and its
source. Some legitimate records may lack an abstract or direct URL, so those
fields remain optional.

### Normalise whitespace without provider-specific identifier formats

**Decision:** Text whitespace is collapsed to single spaces. Source identifiers
must be non-empty after normalisation but are not required to match DOI, PubMed,
or other patterns.

**Reasoning:** This cleans common input inconsistencies while keeping the module
usable with multiple future research sources.

### Allow years from 1 through current year plus one

**Decision:** Valid publication years must be whole numbers in the inclusive
range `1` to `date.today().year + 1`.

**Reasoning:** The upper allowance accommodates early online publications
without accepting implausibly distant years. The lower bound prevents zero and
negative values without assuming a modern-only literature corpus.

### Return a consistent error for non-convertible digit strings

**Decision:** If a string passes `.isdigit()` but `int(...)` cannot convert it,
the validator catches `ValueError` and raises `MetadataValidationError`.

**Reasoning:** Callers should receive the module's documented validation error
for invalid publication-year input. This fixes the Unicode superscript-digit
case without broadening the existing digit-only-string acceptance rule.

### Use a project-specific validation exception

**Decision:** Validation errors use `MetadataValidationError`, a subclass of
`ValueError`.

**Reasoning:** Callers can distinguish bad metadata from unrelated failures and
display or log a meaningful ingestion error.

### Use `unittest` with a source-directory import path

**Decision:** Tests use the built-in `unittest` framework and run successfully
with `PYTHONPATH=src python3 -m unittest discover -s tests -v`.

**Reasoning:** This keeps the first milestone dependency-free. The environment
did not provide a `python` command, so `python3` was required locally.

### Keep PubMed configuration separate from the client

**Decision:** `PubMedClientConfig` owns application identification, developer
contact, optional API key, timeout, endpoint, and request-rate configuration.
`PubMedClient` owns requests, parsing, and result mapping.

**Reasoning:** NCBI identification and rate limits are operational settings,
not citation-processing rules. This keeps secrets out of code and lets tests
construct controlled configuration without relying on environment state.

### Use ESearch history followed by paged EFetch

**Decision:** Searches use ESearch with `usehistory=y`, then EFetch by
`WebEnv`/`QueryKey` for one requested page of up to 20 records.

**Reasoning:** ESearch supplies an accurate total count before records are
retrieved. The history server makes page retrieval efficient and avoids a
separate EFetch request for every citation.

### Limit fallback to title searches

**Decision:** `search_title` uses `[ti]`, then uses `[tiab]` exactly once only
when the title query has zero results. Other search modes never fall back.

**Reasoning:** This balances title precision and recall without unexpectedly
broadening author or identifier lookups.

### Keep PubMed-specific details outside `PaperMetadata`

**Decision:** Each `PubMedSearchRecord` contains validated `PaperMetadata`
plus PMID, DOI, journal, and raw citation-date data.

**Reasoning:** `PaperMetadata` remains a reusable ingestion model while PubMed
details remain explicit and do not shape future non-PubMed sources.

### Define publication year as the primary journal-citation year

**Decision:** Use `JournalIssue/PubDate/Year`; only if it is absent, extract
the first ASCII four-digit year from `MedlineDate`. A present malformed year,
or no usable primary citation date, invalidates that record. `ArticleDate` and
publication-history dates are not substitutes.

**Reasoning:** Online-first, print, and history dates can differ. The rule
makes `PaperMetadata.publication_year` deterministic and keeps its validation
at the existing metadata boundary. PubMed's `[pdat]` search index is related
but not guaranteed to equal the displayed primary citation year.

### Exclude abstracts and full text from Milestone 2A results

**Decision:** The client sets `PaperMetadata.abstract` to `None`, does not
persist API responses, and does not retrieve PMC or publisher full text.

**Reasoning:** PubMed abstracts may be publisher- or author-copyrighted.
Avoiding their display and storage until a rights review keeps this milestone
within a conservative bibliographic-metadata scope.

### Require NCBI identification and make attribution available

**Decision:** A client requires a configured `tool` name and developer email,
enforces a conservative local request rate, and returns attribution/disclaimer
information with search pages.

**Reasoning:** NCBI expects identified, rate-limited API use and visible
disclaimer/copyright information in services using E-utilities. A future UI
must render the supplied information and must not imply NCBI/NLM endorsement.

### Keep identified E-utilities requests on the official secure host

**Decision:** `base_url` must use HTTPS and the exact
`eutils.ncbi.nlm.nih.gov` host. Timeout and request-rate values must also be
finite positive numbers.

**Reasoning:** Every request carries the application tool name and developer
email, and may carry an API key. Restricting the configured destination prevents
those values from being sent to an arbitrary or lookalike host. Rejecting `NaN`
and infinity keeps the timeout and local rate limiter meaningful.

### Share rate-limit scheduling by process authentication scope

**Decision:** Milestone 2B uses one process-local scheduler for anonymous
clients and one for each API-key digest. The scheduler locks the complete
slot-reservation step: checking the next slot, sleeping when needed, and
reserving the next slot. It does not hold that lock while performing HTTP
transport.

**Reasoning:** The rate rule applies to requests, not to one Python object. A
process-shared authentication scope prevents several client instances from
each independently using the full allowance. Digesting API keys avoids keeping
their raw values in the scheduler registry. Releasing the lock before I/O
avoids serialising the response wait itself.

### Retry only bounded transient PubMed failures

**Decision:** The default is two retries with a 0.5-second exponential-backoff
base. Timeouts, transport failures, and HTTP 408, 429, 500, 502, 503, and 504
are eligible. Numeric `Retry-After` values set a minimum delay. Every retry
uses the rate limiter again.

**Reasoning:** This recovers from short-lived service or network problems
without retrying malformed responses, invalid local input, configuration
errors, or permanent HTTP failures. Bounded retries prevent unending requests
and are deterministic to test through injected transport, clock, and sleeper
dependencies.

### Redact upstream XML error text from public exceptions

**Decision:** NCBI XML `<ERROR>` payloads raise a generic
`PubMedResponseError` without including server-provided text.

**Reasoning:** Upstream services can echo query parameters. Returning their
text could expose an API key or developer details in application logs or user
messages.

### Treat missing EFetch citation records as a response failure

**Decision:** After a positive ESearch count and an in-range page request, a
well-formed EFetch response with no `PubmedArticle` elements is a
`PubMedResponseError`. This includes an empty `PubmedArticleSet`.

**Reasoning:** A successful empty page would contradict the history-backed
search result and conceal an unexpected response shape. This does not change
the handling of individual malformed articles, which are still skipped and
counted.

### Keep public search methods within their advertised query semantics

**Decision:** Title, title/abstract, and author methods accept only plain text
and reject PubMed field/grouping/phrase syntax and uppercase Boolean operators.
DOI and PMID methods validate one identifier and do not accept query syntax.

**Reasoning:** Escaping every valid PubMed query variation while retaining
literal punctuation is brittle. A small explicit validation policy preserves
common citation text and prevents a caller from silently broadening or
redirecting a field-specific search. An advanced-query feature, if needed,
requires separate scope approval.

### Keep the search pipeline as a thin ingestion adapter

**Decision:** `SearchPipeline` owns only request routing and a consistent
metadata-only result shape. `PubMedClient` remains responsible for query
validation, rate limiting, retries, HTTP transport, XML parsing, and citation
mapping.

**Reasoning:** This gives callers one stable entry point without duplicating
source-specific integration rules. Future sources can be adapted to the same
result shape without changing the PubMed client or adding retrieval features.

### Standardise citations and deduplicate only on connected identifiers

**Decision:** Pipeline results expose normalised PMID, DOI, title, authors,
journal, publication year, and abstract-availability fields. Records merge
when they share a PMID or DOI, including transitive links through a record that
contains both. Missing fields become `None` or an empty author tuple. Invalid
pipeline records are counted and skipped while valid records continue.

**Reasoning:** PMID and DOI are reliable identifiers when present. A PMID-only
record and a DOI-only record without a shared bridge cannot safely be declared
the same paper, so both are retained. This prevents data loss from speculative
title-based matching while still collapsing known duplicates.

### Preserve abstract availability without retaining abstract text

**Decision:** PubMed XML `AbstractText` presence is recorded as a boolean on a
PubMed search record and carried to the pipeline result. The abstract text
remains excluded from `PaperMetadata` and pipeline results.

**Reasoning:** Availability is useful citation metadata, but it does not
require displaying or storing publisher-supplied abstract content. Deriving the
flag from the XML keeps it accurate even though later stages intentionally
discard the text.

### Distinguish the source total from current-page normalised records

**Decision:** `SearchResult.total_count` remains PubMed's source-wide match
count. `SearchResult.normalised_count` is the number of valid, unique records
returned for the current page after pipeline validation and deduplication.

**Reasoning:** Pagination means one response cannot contain every PubMed match,
and invalid or duplicate records can reduce the records available to callers on
that page. Keeping both counts avoids implying that a page's records represent
the entire result set.

## Confirmed trade-offs

### Unknown keys are ignored

`normalise_paper_metadata` reads only recognised keys and silently ignores
additional ones.

**Benefit:** Inputs can contain source-specific extra metadata without breaking
the current normaliser.

**Cost:** A misspelled required/optional key may go unnoticed; for example,
`"souce_url"` is ignored rather than reported.

### URL validation is deliberately basic

The source URL check only requires an HTTP(S) scheme and non-empty host after
`urlsplit`.

**Benefit:** It is lightweight, local, and avoids network calls.

**Cost:** It does not prove that a URL resolves, belongs to a trusted research
provider, is safe to fetch, or is fully syntactically valid beyond the basic
check.

### Authors must be sequences, not arbitrary iterables

**Benefit:** Lists and tuples are straightforward to validate and safely turn
into a stored tuple.

**Cost:** A generator of author names is rejected even if it could yield valid
names.

### Whitespace normalisation changes formatting

**Benefit:** Titles, names, abstracts, and identifiers have consistent output.

**Cost:** Repeated or unusual whitespace in a source identifier is replaced by
single spaces. Non-whitespace identifier characters are otherwise preserved.

### Boolean operators need a following separator

**Decision:** In local Boolean search, `AND`, `OR`, and `NOT` are operators
only when followed by whitespace, a parenthesis, a quote, or the end of the
query.

**Benefit:** Hyphenated words such as `not-for-profit` are searched as terms.

**Cost:** A query like `NOT-sleep` is a single term, not a negation; use
`NOT sleep` or `NOT(sleep)`.

### A blank API key environment variable means no key

**Decision:** `from_environment` treats a blank or whitespace-only
`PUBMED_NCBI_API_KEY` as unset, using the anonymous rate limit.

**Benefit:** `.env` templates with an empty key line work without editing.

**Cost:** A key accidentally left blank is not reported; the client silently
uses the lower anonymous rate. Passing `api_key=""` directly is still rejected.

### Contradictory identifiers are never merged in the pipeline

**Decision:** Pipeline deduplication joins records that share a PMID or DOI
only when the joined group would not hold two different PMIDs or two
different DOIs.

**Benefit:** Two PubMed records sharing a DOI (for example an article and its
erratum) both stay visible instead of one PMID being silently dropped.

**Cost:** Such records reach storage as separate papers, where
`PaperStore.save` raises `PaperIdentityConflictError` for the second one.

### Legacy DOI lookups are filtered by SQLite

**Decision:** DOI matching asks SQLite for the exact normalised DOI plus only
rows that look un-normalised (no `10.` start, ASCII uppercase, or trailing
whitespace), instead of reading every row into Python.

**Benefit:** Saves and lookups stay fast as the database grows.

**Cost:** A legacy DOI whose only difference is non-ASCII uppercase is not
matched. `save` has always stored normalised DOIs, so this needs data written
by other tools.

### Book records are counted, not returned

**Decision:** `PubmedBookArticle` records are not mapped to `PaperMetadata`.
Each is added to `invalid_record_count`, and a page containing only books is
returned as an empty page.

**Benefit:** Small change; counts stay honest and a books-only page no longer
raises an error.

**Cost:** Books and chapters (for example StatPearls) cannot be found through
this client, and "invalid" is an imprecise label for "unsupported".

### Citations may have no authors

**Decision:** `PaperMetadata.authors` may be an empty tuple. The field is still
required and must be a sequence of non-empty text.

**Benefit:** Real PubMed records without an author list are returned and can
be stored.

**Cost:** Code that displays or cites a paper can no longer assume a first
author exists.

### One module owns identifier spellings

**Decision:** `identifiers.py` defines DOI-prefix removal and PMID
leading-zero removal, used by the client, pipeline, and storage. Each layer
keeps its own validation and error type.

**Benefit:** All layers agree on which spellings are the same paper.

**Cost:** The DOI format pattern itself is still defined separately in each
layer; `PaperStore` scans zero-padded legacy PMIDs with a `GLOB '0*'` clause.

### Attribution follows a recorded source, not an assumption

**Decision:** Each `StandardisedPaper` and stored row has a `source_name`.
The pipeline sets it to `"PubMed"`; `StoredPaper.attribution` returns
`PUBMED_ATTRIBUTION` only when the recorded source is PubMed.

**Alternative rejected:** attaching PubMed attribution to every stored paper.
It needs no database change but would wrongly credit PubMed for a paper from
another source and would have to be redone for a second source.

**Benefit:** Attribution survives storage, cannot be credited to the wrong
source, and a second source needs only a new name and attribution.

**Cost:** One new database column and an automatic upgrade step. A paper
built by hand without a source has no attribution, so callers must set
`source_name` themselves if such a paper is PubMed-derived.

### Older databases are assumed to hold only PubMed citations

**Decision:** When `PaperStore` opens a database without the `source_name`
column, it adds the column and marks every existing row as PubMed.

**Reasoning:** PubMed was the only source this project could ingest before
the column existed.

**Cost:** A row inserted by another tool from a different source would be
mislabelled as PubMed.

### Updates never erase a recorded source

**Decision:** `PaperStore.save` normally overwrites fields with incoming
values, including `None`, but an incoming `source_name` of `None` keeps the
stored source.

**Benefit:** Re-saving a paper from a hand-built record cannot strip its
required attribution.

**Cost:** `source_name` behaves differently from the other fields, and a
source cannot be cleared through `save`.

## Phase 1: command line and service layer

### argparse, not typer

**Decision:** The `hri` command line uses `argparse` from the standard library.

**Reasoning:** typer would be the project's first third-party dependency, and
the commands are simple enough not to need it.

**Cost:** More code per command, and plainer help output.

### A service layer that returns objects, with a thin CLI on top

**Decision:** `hri/services.py` holds every operation as a plain function that
returns a frozen dataclass and never prints. `hri/cli.py` only parses
arguments, calls those functions, and formats text; a test checks that it
imports nothing else from the project.

**Benefit:** The future UI and API call exactly what the CLI calls, so
behaviour cannot drift between them, and the functions are testable without
capturing output.

**Cost:** Two layers to change for a new command. Each call opens its own
database connection, which is simple and thread-safe but slower than sharing
one.

### One error type for bad input

**Decision:** Lower layers raise several `ValueError` subclasses for bad
arguments. The service layer reports all of them as `InvalidRequestError`.

**Benefit:** A UI catches one error for "ask the user to fix their input".

**Cost:** The original error type is only available as the chained cause.
`PubMedConfigurationError` is deliberately left separate, because missing
settings are not something the end user typed.

### Default database under `data/`, overridable

**Decision:** The database path is `--db`, else `HRI_DB_PATH`, else
`data/hri.sqlite3` relative to the current folder. This replaces the earlier
rule that nothing writes a database inside the repository by default.

**Benefit:** `hri` works with no configuration, and the file is easy to find.

**Cost:** The data lives inside the working copy, protected only by
`.gitignore`, and the default depends on which folder the command runs from.

### `pyproject.toml` and `requirements.txt` both exist

**Decision:** `pyproject.toml` defines the installable package and the `hri`
console script, and declares Python 3.9 as the minimum. `requirements.txt` is
kept for hosts that install from it. A test fails if their dependency lists
differ or a requirement is not pinned.

**Cost:** Two files to edit for each dependency. Building the package needs
setuptools at install time, which pip downloads; it is not a runtime
dependency.

### Ingest reads a bounded number of PubMed positions

**Decision:** `ingest` pages through results 20 at a time and reads at most
`max_records` positions (capped at 1000), then stops, even if some of those
records were invalid.

**Benefit:** The number of requests is known before starting, which keeps the
load on NCBI predictable.

**Cost:** `--max 200` can save fewer than 200 papers when PubMed returns books
or unusable records. Each page repeats the search request, so 200 records cost
about 20 requests.

### Identifier conflicts during ingest are counted, not fatal

**Decision:** When a fetched record's PMID and DOI point at two different
stored papers, `ingest` skips it, counts it in `conflicts`, and continues.

**Benefit:** One contradictory record does not abort a long ingest.

**Cost:** The skipped record is only visible as a number; which record it was
is not reported.

### Saved lists reference papers; they do not copy them

**Decision:** `saved_list_items` stores the database id of a paper. List names
are unique ignoring case, a paper appears at most once per list, and order is
the order added.

**Benefit:** A list always shows a paper's current metadata and attribution.

**Cost:** Lists cannot hold papers that are not stored. There is no delete or
rename yet, and lists record no dates.

### Versioned schema upgrades

**Decision:** `schema.py` records the layout version in SQLite's
`PRAGMA user_version` and applies upgrade steps in order. A database with a
higher version than the code knows is refused.

**Benefit:** Older databases upgrade in place without losing rows, and old
code cannot silently damage a newer database.

**Cost:** Every layout change needs a new numbered step and an upgrade test.

### Export formats

**Decision:** Export offers CSV and a plain numbered citation layout. CSV
cells that begin with `=`, `+`, `-`, or `@` are prefixed with `'`. The
citation text ends with the PubMed attribution; the CSV carries a source
column and link, with the attribution returned beside it.

**Benefit:** CSV opens safely in spreadsheet programs, and the citation text
is self-contained.

**Cost:** The citation layout is not a formal style (Vancouver, APA), and a
CSV saved to disk does not itself contain the attribution text.

## Before Phase 2: citations, list management, and concurrency

### Vancouver (NLM) is the citation format; plain is kept

**Decision:** `--format citations` produces Vancouver (NLM) style, the
standard in medical publishing. The earlier simple layout is `--format plain`.
CSV remains the default export format. APA is not provided.

**Cost:** The output differs from a PubMed-generated citation in small ways:
the date is the year only (the full date is not stored), DOIs are lower case,
and only journal articles are covered because books are not stored.

### Citation details are stored as one JSON column

**Decision:** Vancouver needs NLM author names, the journal abbreviation,
volume, issue, and pages. They travel together as one `CitationDetails`
object and are stored in a single `citation_json` column (schema version 3).

**Alternatives rejected:** deriving `Polack FP` from the stored
`Fernando P Polack` by splitting on spaces, which mangles multi-word surnames
and group names such as `WHO Study Group`; and replacing the stored full
names with the NLM form, which would lose first names for display and search.

**Benefit:** One field per layer instead of five, and full names are kept.

**Cost:** The details cannot be searched or filtered in SQL. Papers saved
before version 3 have none and fall back to full names and the full journal
title until they are ingested again. Unlike `source_name`, a later save
without details clears them, following the general overwrite rule.

### Author display names use the forename, not forename plus initials

**Decision:** A stored author name is `ForeName LastName`; `Initials` are
used only when the forename is missing.

**Reasoning:** PubMed sends both, and joining them produced
`Fernando P FP Polack`. Test fixtures never included both elements, so the
defect was invisible until citation formatting was examined.

**Cost:** Papers ingested before the fix keep the doubled initials until they
are ingested again.

### Deleting a list never deletes papers

**Decision:** `delete_list` removes the list and its memberships only.
Deleting papers is not offered.

**Benefit:** A list operation cannot destroy ingested data or affect another
list.

**Cost:** Papers nobody lists stay in the database. Deletion has no undo, and
the service does not ask for confirmation; a UI must.

### WAL mode and a busy timeout for concurrent writers

**Decision:** `PaperStore` sets `PRAGMA journal_mode = WAL` when it opens a
database, and every connection waits up to 10 seconds
(`BUSY_TIMEOUT_SECONDS`) for another writer.

**Benefit:** Two commands writing at once queue instead of failing, and
readers are not blocked by a writer.

**Cost:** SQLite keeps `-wal` and `-shm` files beside the database (ignored
by Git), and WAL does not work reliably on network file systems. A writer
blocked for more than 10 seconds still fails.

## Phase 2: open-access full text

The content rules themselves are in `docs/CONTENT_POLICY.md`. This section
records why the access method and design were chosen.

### Access method: PMC Cloud Service for licence and text

**Context (checked against NCBI's documentation on 2026-11-17):** NCBI permits
only four services for automated retrieval of PMC content: the PMC Cloud
Service, PMC OAI-PMH, E-utilities, and the BioC API. The PMC OA Web Service
(`oa.fcgi`), which most older tools use, and the legacy FTP article packages
were retired the week of 24 August 2026.

| Option | Licence | Text | Verdict |
| --- | --- | --- | --- |
| PMC Cloud Service (S3 bucket `pmc-oa-opendata`, anonymous HTTPS) | `license_code` in a small per-version metadata JSON, plus `pmid`, `doi`, `is_pmc_openaccess`, `is_retracted` | JATS XML per version | **Chosen** for licence and text |
| OAI-PMH `GetRecord`, `metadataPrefix=pmc` | Only inside the XML | JATS XML, OA only | Not chosen: licence must be parsed from the text we might reject |
| E-utilities `efetch db=pmc` | Only inside the XML | Same XML | Not chosen for text, for the same reason; used only for searching |
| BioC API | Not clearly exposed | Pre-split passages | Not chosen: less standard, licence unclear |

**Decision:**
- Find articles for a query with E-utilities `esearch db=pmc` and
  `open access[filter]`.
- Link identifiers and find the current version with the PMC ID Converter.
- Read the licence from the version's Cloud metadata JSON, and download the
  XML only when the licence is allowed.

**Benefit:** Text under a rejected licence is never downloaded, and the
licence is a clean field rather than parsed prose.

**Cost:** Two new hosts, and three requests per stored article (converter,
metadata, XML).

### Exact endpoints

Recorded from NCBI's documentation on 2026-11-17:

- **PMC ID Converter:** `https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/`
  with `ids` (comma-separated, at most 200, all of one type), `idtype`
  (`pmcid`, `pmid`, `mid`, or `doi`), `versions=yes`, `format=json`, `tool`
  (no spaces), and `email`. The page documents the `current` version flag only
  in its XML example; a live JSON request on 2026-11-17 returned
  `records[].versions[].pmcid` and `records[].versions[].current`, and the
  client relies on that JSON shape. No API key is documented, so none is sent.
- **Cloud metadata:**
  `https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC<n>.<v>.json`, fields
  `pmcid`, `version`, `pmid`, `doi`, `title`, `citation`,
  `is_pmc_openaccess`, `is_manuscript`, `is_retracted`, `license_code`,
  `xml_url`.
- **Cloud XML:**
  `https://pmc-oa-opendata.s3.amazonaws.com/PMC<n>.<v>/PMC<n>.<v>.xml`.
- **Search:** `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi`
  with `db=pmc`, returning PMC UIDs (the PMCID without `PMC`).

### The PMC client has its own fixed host allowlist

**Decision:** The PMC client accepts exactly three HTTPS hosts and builds every
URL itself. `tool`, `email`, and the API key are added only for NCBI hosts.
The PubMed client keeps its single-host restriction.

**Cost:** A wider network surface than before, mitigated by the fixed list and
by never sending credentials to Amazon.

### Shared rate limiting and retries

**Decision:** The rate limiter and retry loop move from `pubmed.py` into
`ingestion/http.py` and are used by both clients. Both NCBI hosts share one
process-wide limit (3 requests per second, or 10 with an API key). The S3 host
has its own limit of 3 per second, because Amazon publishes no rate for this
bucket and polite pacing costs little.

### Only the current version, found with the ID converter

**Decision:** The version the ID converter marks `current` is the only one
used. If it is not in the Open Access bucket, the article is counted as
"not in OA subset (current version unavailable)" and nothing is stored. The
version identifier is stored with the document.

**Reasoning:** NCBI warns that a higher version number does not mean a newer
version. In a check on 2026-11-17, `PMC12805672.1` was current while only
`.2`, an author manuscript, was in the bucket.

**Cost:** Some articles with an available but non-current version are skipped.

### Retracted articles are rejected; corrections are flagged

**Decision:** An article is rejected as `retracted` if its metadata says
`is_retracted` or its XML links to a `retraction-forward`. Corrections, errata,
partial retractions, and expressions of concern are stored with
`notice_flags`. `recheck_fulltext` re-reads the metadata of every stored
document and deletes any that have since been retracted.

**Cost:** Correction flags depend on publishers tagging forward links, which
JATS describes as rarely used, so they are best effort. PubMed's
`CommentsCorrections` data would be more complete and is a possible later
improvement. The re-check covers retraction only; refreshing correction flags
would mean downloading the XML again.

### Chunk size measured in approximate tokens

**Decision:** Without a tokenizer (standard library only), a token is
approximated as a word or a punctuation mark. Chunks aim for about 400 such
tokens (never more than 500) with a 50-token overlap, break at sentence ends
where possible, and never cross a section boundary.

**Cost:** Real model tokenizers count differently, typically more tokens for
the same text. Phase 3 should re-measure with the embedding model's tokenizer.

### Implementation choices made while building Phase 2

- **Query search looks past the limit.** `fetch_fulltext --query` asks PMC for
  up to 200 matches (one request) and processes up to `--max` new ones.
  Otherwise a second run would find only the articles the first one stored.
- **A version that leaves the Cloud Service keeps its text.** The licence it
  was stored under still applies; only retraction removes stored text.
- **Unparseable XML is a failure, not a rejection.** It is counted and logged
  but not recorded in `fulltext_rejections`, because the licence allowed it
  and a later run may succeed.
- **Unclear Cloud status fields fall on the safe side.** A missing
  `is_pmc_openaccess` counts as not open access, and a missing `is_retracted`
  counts as retracted.
- **Section names come from headings first.** Publishers sometimes set
  `sec-type` wrongly (one fixture tags "Discussion" as `conclusions`).
  Headings that match no canonical name, common in reviews, become `other`
  and keep the original heading.
- **Full-text services live in their own module**, re-exported by
  `hri.services` so a UI still has one import point. The shared input error
  moved to `hri/errors.py` to avoid a circular import.
- **Attribution is written once, at storage time.** It names up to six
  authors, the title, the PMC citation, the stored version, the licence and its
  URL, the PMC page, the retrieval date, and the changes made, as CC BY
  requires.

## Phase 3: keyword search over stored passages

### Keyword (BM25) search now; embeddings later

**Context:** The README plans hybrid (keyword plus meaning-based) search.
Meaning-based search needs an embedding model. The options were a library
such as sentence-transformers or fastembed (a new dependency of hundreds of
MB plus a model download from Hugging Face, a host outside the allowlist),
or a standard-library stand-in such as hashed TF-IDF vectors (no real
understanding of meaning).

**Decision (approved by the owner on 2026-11-18):** Build keyword search
only, with SQLite's built-in FTS5 index and its BM25 ranking. Embeddings are
a separate later phase that needs its own dependency and host approval.

**Benefit:** No dependency, no new host, and it works on Python 3.9.6
(SQLite 3.43) and 3.15 (SQLite 3.54).
**Cost:** A passage must contain the query's words (after stemming);
synonyms such as "heart attack" and "myocardial infarction" do not match.

### How the index is built

- Schema version 5 adds `chunks_fts`, an FTS5 table with `content='chunks'`,
  so the text is not stored twice. Insert, delete and update triggers on
  `chunks` keep it in step; the upgrade from version 4 rebuilds it from the
  passages already stored.
- Because deletion goes through the triggers, a retracted or replaced article
  leaves search in the same transaction that removes its text.
- Tokeniser: `porter unicode61 remove_diacritics 2`, so case, accents, and
  English word endings are ignored.

### How a query is read

- The query is split into words (`\w+`); each word is put in double quotes
  and all must match. Quoting means nothing the caller types is FTS5 syntax,
  so input cannot cause syntax errors or change the search. Operator words
  (`OR`, `NOT`, `NEAR`) are ordinary words.
- All words must match (AND), matching the existing keyword search over
  citations. Results are ordered by BM25, then by storage order for ties.
- Search lives in `FullTextStore`, which owns the SQL, as `PaperStore` owns
  citation search. The service is `search_passages` in
  `hri/fulltext_services.py`. `src/retrieval/` stays empty until there is
  ranking logic (such as hybrid fusion) that is not storage.

## Phase 3B: meaning-based (embedding) search

### Dependency and Python versions

**Decision (approved by the owner on 2026-11-18):** `fastembed==0.9.1`
(ONNX Runtime, no PyTorch) as the optional extra `.[embeddings]`, pinned in
`pyproject.toml` and `requirements.txt` with the marker
`python_version >= '3.10'`.

**Why optional:** fastembed 0.9.1 needs Python 3.10+, and ONNX Runtime stopped
publishing Intel-Mac builds after 1.23.2 (Python 3.10 to 3.13). The project's
`python3` is 3.15 and the compatibility target is 3.9, so the core stays
standard-library-only and falls back to keyword search without fastembed.
The embedding environment on the owner's Mac is a Homebrew Python 3.13.18
virtual environment, `.venv-embed` (ignored by Git).

Installed and checked on 2026-11-18: fastembed 0.9.1, onnxruntime 1.23.2,
numpy 2.5.5, tokenizers 0.23.3, huggingface_hub 1.33.0.

**Dependency pins (2026-11-19):** every package the extra installs is pinned
in `constraints-embeddings.txt`, applied with
`pip install -e '.[embeddings]' -c constraints-embeddings.txt`. Constraints
rather than install requirements, so the pins never force anything on an
install without the extra and `requirements.txt` stays the list of direct
dependencies. The list was taken from the tested Python 3.13 environment and
is valid there; other Python versions may need different versions (numpy 2.5
needs 3.11+). Hashes are not pinned.

### Model

Candidates compared (sizes from Hugging Face, memory estimated):

| Model | Download | Dimensions | Approx. memory | Max input |
| --- | --- | --- | --- | --- |
| **BAAI/bge-small-en-v1.5** (chosen) | 67.4 MB | 384 | ~250 MB | 512 tokens |
| sentence-transformers/all-MiniLM-L6-v2 | 91.3 MB | 384 | ~280 MB | 256 tokens |
| BAAI/bge-base-en-v1.5 | 218.8 MB | 768 | ~600 MB | 512 tokens |

**Decision:** BAAI/bge-small-en-v1.5: near bge-base retrieval quality at a
third of the size. MiniLM was rejected because 256 tokens would cut most of
our ~400-token passages in half.

| Pinned value | |
| --- | --- |
| Repository (fastembed's ONNX export) | `Qdrant/bge-small-en-v1.5-onnx-Q` |
| Revision | `aa8f8b060edb00e03bfdd08813a2949946c8ba55` |
| Model file | `model_optimized.onnx`, 66,465,124 bytes |
| SHA-256 | `51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431` |
| Other files | `config.json`, `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json` (about 0.7 MB) |
| Licence | MIT |

The values live in `src/retrieval/embeddings.py`. A model file whose size or
hash differs is deleted and the model reported unavailable.

### Hosts

Hugging Face is contacted **only to download the pinned model files**, by
`retrieval.embeddings.prepare` (the `embed-passages` command and
`prepare_embedder`), never by a search and never with user data. Observed on
2026-11-18:

- `huggingface.co`: file listing and the small files, then a redirect for the
  model file;
- `us.aws.cdn.hf.co`: the model file itself (Hugging Face's file CDN; the
  `*.cdn.hf.co` name may differ by region).

`HF_HUB_DISABLE_TELEMETRY=1` and `HF_HUB_DISABLE_XET=1` are set before the
library loads. Without them, huggingface_hub 1.33 also requested
`/api/agent-harnesses` (to tag requests with the calling AI tool) and fetched
the model through Xet storage hosts (`cas-server.xethub.hf.co`).

### Query and passage embedding

Passages use fastembed's `passage_embed`; questions use `query_embed` with
BGE's instruction `Represent this sentence for searching relevant passages: `
in front, because fastembed 0.9.1 adds no instruction for this model.

### Long passages

Measured with the model's tokenizer on the 106 stored live-check passages:
**5 (4.7%) exceed 512 tokens; the longest is 523**, so at most 11 tokens are
cut off. The chunker's approximate count is about 19% lower than the model's
count. `embed_passages` logs this every run; if it grows, lower the chunk
target.

### Fusion and fallback

- Reciprocal rank fusion (k = 60) of the top 50 keyword and top 50 vector
  matches. No score tuning needed.
- `search_passages` never downloads: it uses a model only if already
  prepared, and otherwise runs keyword-only with a `notice` saying why.
  `prepare_embedder()` is the start-up hook that downloads once.
- Vectors are stored as float32 blobs in `chunk_embeddings` (schema 6), keyed
  by model and revision, and deleted by trigger with their passage.
- shortcut: similarity is computed in pure Python against every vector, fine
  to about 50,000 passages; past that, add a vector index.

## Phase 4: answers with checked citations

### Providers and models (checked 2026-11-19)

From Google's current pricing and model pages (`aistudio.google.com/docs`):
`gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite` and
`gemini-3.1-flash-lite` are **Stable** and **free of charge** on the free
tier; on the free tier, content is **"used to improve our products"** (paid
tier: no). `gemini-2.5-flash` is deprecated and routes to another model from
1 December 2026. Google's documentation **no longer lists free-tier rate
limits per model**: they are shown only in each account's AI Studio page.
Exceeding a limit returns HTTP 429 `RESOURCE_EXHAUSTED`.

From Groq's docs (`console.groq.com/docs`): production models include
`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `openai/gpt-oss-120b` and
`openai/gpt-oss-20b`; free-plan limits are shown only in the account; a rate
limit returns HTTP 429 with `retry-after`.

**Decision (owner, 2026-11-19):** default `gemini-3.5-flash-lite` (Flash-Lite
models usually get the most free requests); fallback
`llama-3.3-70b-versatile`. Both can be changed with `HRI_GEMINI_MODEL` and
`HRI_GROQ_MODEL`.

### Design

- **No SDK, no new dependency.** Both APIs are one JSON POST each, made with
  `urllib`. Keys go in headers. A non-default User-Agent is sent because some
  API front ends reject urllib's.
- **Fallback on any provider failure**, not only 429: timeouts, outages and
  bad replies also move on to the next provider. If all fail, the answer is a
  "busy" abstention, never an exception.
- **One retry per provider** after 2 seconds for HTTP 500, 502, 503, 504 or a
  network failure, added after the live check hit a transient Gemini 503.
  Never for 429, so an exhausted free quota is not requested again. Each
  attempt counts against the daily cap only once per provider.
- **Titles are not evidence.** The live check showed the model citing an
  article title (worded as a question) as support for "Yes"; title passages
  are now never sent. The title still appears in every citation.
- **Free-tier protection:** a reply cache keyed by the normalised question,
  the exact passage IDs, and a prompt version (schema 7, `answer_cache`);
  a daily request cap (`HRI_LLM_DAILY_REQUEST_CAP`, default 100, counted per
  provider attempt in `llm_usage`). When the database cannot be written, both
  continue in memory.
- **Environment variables only.** Streamlit secrets were not added (owner's
  decision): reading them needs Streamlit; a Streamlit app can copy its
  secrets into the environment.
- **Only CC0 and CC BY passages are sent**, whatever the storage policy
  allows, because the free tier uses content to improve products.

### Retrieval for questions

Questions contain filler words ("what does the evidence say"), so the
keyword half uses **any-word** BM25 (`match_all=False`) rather than the
all-words search of `hri search-passages`; BM25 gives common words little
weight. It is fused with meaning-based search when the model is ready.

### Abstention

| Check | When | Threshold |
| --- | --- | --- |
| Too few passages | before calling the model | fewer than 2 |
| Off-topic | before calling the model, hybrid only | closest passage cosine < 0.7 |
| Model says so | after | reply starts with `INSUFFICIENT_EVIDENCE` |
| No valid citation | after | 0 |
| Mostly invented citations | after | invalid > valid |

The 0.7 threshold was measured with bge-small on the 5-article live-check
collection: on-topic questions scored 0.72 to 0.87, medical questions on
topics not in the collection 0.60 to 0.67, and non-medical ones 0.41 to 0.48.
It is a small sample and must be recalibrated on a larger collection.

No numeric confidence score is given: none would be calibrated. `abstained`
and `abstain_reason` say whether, and why, no answer was given.

### Prompt injection

Retrieved passages are untrusted. Each is wrapped in
`<evidence id="n" source="...">` and `</evidence>`; any `<evidence` or
`</evidence` text inside a passage or the question is altered (`‹`) so it
cannot close its block or open another. The system prompt says the rules
cannot be changed by evidence text. Citation checking still removes any
citation an injected passage tricks the model into inventing.

## Phase 5: evaluation

- **A separate evaluation database** (`data/eval.sqlite3`, ignored by Git),
  approved by the owner, built from a copy of the owner's 19 citations: their
  open-access full text (6 of 19 allowed: 9 rejected by licence, mostly
  CC BY-NC-ND, and 4 not in PMC) plus three PMC searches (GLP-1
  cardiovascular, SGLT2 heart failure, metformin cognition), giving 18
  articles and 497 passages. The main database is untouched.
- **JSON, not YAML**, for question sets: YAML needs a dependency.
- **Retrieval is scored per article**, the unit a citation points to:
  passages are reduced to the order in which distinct articles first appear.
  Precision@k divides by k even when fewer results exist, and is capped by
  the number of relevant articles (one for most questions, so at most 20% at
  k = 5).
- **Keyword retrieval in the evaluation is any-word BM25**, the variant
  `ask_question` uses, not the all-words search of `hri search-passages`.
- **Judge on Groq** (owner's decision, 2026-11-19, replacing the first
  choice `gemini-3.6-flash`, whose free quota ran out after 3 judgements on
  the first run): `openai/gpt-oss-120b` by default (`HRI_JUDGE_MODEL`),
  a different model family from the Gemini answer model.
- **Groq model change (2026-11-19):** `llama-3.3-70b-versatile`, the approved
  Groq fallback for answers, is listed in Groq's documentation but the API
  returns HTTP 404 `model_not_found` for the owner's free key. The key's
  model list includes `openai/gpt-oss-120b`, `openai/gpt-oss-20b` and
  `qwen/qwen3.8-27b`; both `gpt-oss-120b` and `qwen3.8-27b` returned valid
  judge JSON in a test. Defaults are now `qwen/qwen3.8-27b` for the answer
  fallback and `openai/gpt-oss-120b` for the judge, so the judge is a third
  model family. The Groq answer fallback has not yet been tested on real
  questions. A model never
  judges its own answers; such answers are left unjudged and counted. The
  judge rates each cited sentence against only the passages it cites, as
  supported, partial, or unsupported, with each cited passage sent once to
  save tokens. Its prompt is `eval/judge_prompt.txt`. The human spot-check
  sheet remains the real check.
- **Preliminary until reviewed:** while any question is `needs_review`, the
  report is titled "preliminary (questions under review)", and the README
  carries no evaluation numbers (owner's decision).
- **Evidence recall** (relevant articles among the passages actually sent to
  the model) is reported alongside article-level retrieval, because the
  first run showed retrieval at 100% while the evidence sent missed a
  relevant article.
- **Correct abstention excludes "busy" answers**: a provider outage is not a
  judgement about evidence.
- **Free tier:** answers and judgements are cached in the database and
  reused; one question every 5 seconds; `--max-questions`; the daily cap.
- **Spot-check sheets are not committed**: they hold article text without the
  attribution lines CC BY requires.

### Experiment: at most three passages per article (rejected)

**Rule (owner, 2026-11-19):** keep a change only if recall and citation
scores do not get worse against `eval-baseline-v1`, with the same question
set and settings.

| Measure | Baseline | Three per article |
| --- | --- | --- |
| Evidence recall | 92.8% | 93.8% |
| Citation validity | 100% | 100% |
| Claims supported (judge) | 97.5% | 96.2% |
| Claims partial / unsupported | 2.5% / 0% | 3.8% / 0% |
| Answered when expected | 91% | 91% |

**Decision:** rejected and reverted (commits `4b5e952`, revert after it):
support fell, and the target question q22 still lacked its second relevant
article because the freed places went to unrelated articles further down the
passage ranking. Capping passages does not by itself bring in the next
*relevant* article.

**Next ideas, not yet tried:** choose evidence by article first (the best
passage from each of the top-ranked articles, then fill), and score
retrieval per passage so misses like q13 (right article, wrong passage) show
in the numbers.

### Question review (owner, 2026-11-19)

- **Relevance rule:** an article counts as relevant only if it contains
  evidence that helps answer the question, not if it merely mentions the
  topic.
- q20 keeps PMC13280743 (Mendelian randomization reports cardiovascular
  benefit for GLP-1 receptor agonists and metformin). q21 excludes it: its
  SGLT2 results come from genetic proxies of SGLT2 inhibition, not from
  people using the drug; PMC13606989 is excluded too (SGLT2 only a
  covariate). q22 keeps PMC13776795 (hyperkalemia linked to in-hospital
  death). q23 drops PMC13773823, which says trials should assess muscle
  mass, strength and function but not how. q24, q26, q28 and q31 should
  abstain.
- With every question reviewed, the report is no longer marked preliminary.
  Judge agreement stays "pending manual spot-check" until the owner's
  verdicts are recorded.
- The before/after table of the rejected experiment is not repeated in the
  reviewed report: it was measured with the earlier relevance labels. It is
  recorded above and in commit `3269555`.

### Spot-check verdicts and response time (owner, 2026-11-19)

- **Verdict-only export** (`hri eval-verdicts`): question, claim, cited
  PMCIDs, passage IDs (`<PMCID>.<version>:p<position>`, stable for a stored
  version), judge verdict, the owner's verdict and notes. Committed; the full
  sheet with article text stays local (CONTENT_POLICY.md section 11).
- **Judge agreement** is the exact-match share of claims both rated, read
  from every `eval/verdicts-*.csv`; "pending manual spot-check" until the
  owner rates one.
- **Response time:** a fully cached run shows the last real measurement
  with its date (`eval/results/latency.json`, seeded with the first run:
  median 3.6 s, p90 18.9 s, 28 answers) instead of "n/a". `--no-cache`
  measures it again, limited to 10 questions (default 5).

### Experiment: evidence by article first (rejected, 2026-11-20)

Best passage from each of the top four articles always sent, then the
remaining two places filled by rank (commit `34affa1`). Four was chosen from
the evaluation set itself (q20's relevant articles rank 1 to 4, q22's second
article ranks 4), so the experiment was tuned on the data that judged it.

| Measure | Baseline (reviewed) | Articles first |
| --- | --- | --- |
| Evidence recall | 93.5% | 97.8% |
| Answered when expected | 91.3% | 95.7% (q22 answered) |
| Citation validity | 100% | 100% |
| Claims supported / partial / unsupported | 97.5% / 2.5% / 0% | 95.6% / 4.4% / 0% |
| Citations in answers | 211 | 171 |

**Decision:** rejected and reverted under the owner's rule, because the
supported rate fell. The cost came from single-article questions: the
seeded places went to less relevant articles, so answers cited the main
article less (q02 from 4 citations to 1).

**What the two experiments show:** forcing variety helps multi-article
questions and hurts single-article ones. A next attempt would seed an extra
article only when its best passage is nearly as relevant as the top one (a
relevance gap rule), and should be judged on a larger, separately written
question set to avoid tuning on this one.

### Second, held-out question set (2026-11-20)

`eval/questions-set2.json`, 32 questions, written to be less favourable to
the system than set 1: from the articles' body text rather than their
abstracts (except three review articles), in different or everyday wording
("high potassium", "incretin-based injections"), with relevant articles
chosen by reading the text and no search run while writing. It includes four
multi-article questions, four about library papers without stored text, and
three off-topic questions. It was still written by the developer, so it is
not fully independent; questions written by another person would be better.

**Rule:** set 2 is held out. It is used only to confirm a decision (for
example, a change that passed on set 1), never to choose settings. Set 1
remains the development set. Until the owner reviews it, its results are
preliminary.

## Phase 6: deployment (owner's decisions, 2026-11-20)

- **Streamlit Community Cloud** runs the app (Python 3.12). The UI is the
  owner's; the repository ships only a placeholder page that proves the
  deployment works.
- **Optional extras keep the core dependency-free:** `embeddings`
  (fastembed), `app` (streamlit 1.67.0), `api` (fastapi 0.165.0, pydantic
  2.14.4, uvicorn 0.54.0), `dev` (ruff 0.17.6, httpx 0.28.1). The app and API
  need Python 3.11+, because current FastAPI and Streamlit do; the core and
  its tests still run on Python 3.9. `requirements.txt` is the Streamlit
  runtime (embeddings + app), `requirements-dev.txt` the rest. No PyTorch.
- **The FastAPI app (`src/api/`) is the documented REST interface, not
  deployed.** It lives in `src/` so `pip install -e .` installs it and tests
  import it like every other package.
- **Data bundle as a Release asset, always** (not committed even when small):
  a database file committed on every scheduled rebuild would grow the
  repository's history for ever. Published under the fixed release tag
  `data-bundle`, so the app's download URL never changes. The app downloads
  it once per process, checks its SHA-256 against the manifest, and caches it.
- **Collection size about 100 articles:** five topics in `topics.json`, up to
  20 open-access articles and 40 citations each.
- **JSON for the topics file**, not YAML, which would need a dependency.
- **Read-only database mode** (`HRI_DB_READ_ONLY=1`): immutable, no side
  files, no schema upgrades, refusing unmerged `-wal` changes (a read-only
  connection would silently read stale data). Bundles are written in
  rollback-journal mode so they can be opened this way.
- **Runtime writes in memory:** the answer cache and daily cap moved to
  process-wide memory (they had been per instance, so the cap did not hold
  across calls on a read-only database), guarded by a lock for concurrent
  visitors; saved lists and the per-visitor question limit are session
  objects (`SessionSavedLists`, `VisitorQuota`). All reset on restart.
- **AI quota for a public app:** `HRI_LLM_DAILY_REQUEST_CAP` defaults to 50
  in the app (100 locally) and `VisitorQuota` allows 5 questions per
  visitor.
- **Settings:** environment variables, or Streamlit secrets copied into the
  environment at start-up (`hri.app_config`); an existing variable wins.
- **CI:** ruff (rules E4, E7, E9, F, I; no line-length rule) and the tests on
  Python 3.9 (core) and 3.12 (all extras). The bundle workflow runs weekly
  and on demand with NCBI settings from repository secrets.

### PubMed field queries fixed, and the first real bundle (2026-11-20)

- **Bug found while building the bundle (present since Phase 1):** title and
  title/abstract searches sent `(word1 word2 ...)[tiab]`. PubMed reads that as
  the first word in all fields plus the remaining words as one exact phrase,
  so "SGLT2 inhibitor heart failure" found 0 papers instead of 1,496 (since
  2020). Fixed at the root in `pubmed.py`: every word is tagged
  (`SGLT2[tiab] AND inhibitor[tiab] AND ...`); author names stay one quoted
  phrase (`"Polack FP"[au]`). The owner's 19 stored papers were ingested by
  PMID or with single-phrase queries and are unaffected; the evaluation
  database's full text came from PMC searches, which never used field tags.
- **Candidates per topic raised from 20 to 35:** `max_fulltext` counts
  candidates, and about 40% are rejected under CC0/CC BY, so 20 per topic
  gave 55 articles; 35 gives 96, near the approved ~100.
- **Measured bundle:** 14.0 MB (174 citations, 96 CC BY articles, 2,392
  passages and vectors), built in about 10 minutes locally. Small enough for
  a release asset and for Streamlit's memory; 302 passages (12.6%) exceed the
  embedding model's 512 tokens.

### Groq fallback tested; "insufficient evidence" marker on any line (2026-11-20)

Three real questions answered by `qwen/qwen3.8-27b` alone (Gemini switched
off), on a copy of the evaluation database: two answers matched their
passages (all citations valid, figures correct, including a number needed
to treat that the article states). The third, an unanswerable dose question,
was answered honestly but with `INSUFFICIENT_EVIDENCE` on the **last** line,
so the pipeline, which only checked the first line, labelled it answered.
Now any line starting with the marker counts; the explanation is the text
after it, else before it, without citations. None of the 65 cached
evaluation answers had the marker anywhere but the start, so no result
changes. One request first came back "busy", most likely Groq's per-minute
token limit after two quick requests; it succeeded a minute later.

### Off-topic threshold rechecked on the deployed bundle (2026-11-20)

Best cosine similarity per question (bge-small), on the 96-article bundle:
on-topic questions written from its titles 0.81 to 0.92; medical questions on
topics it does not cover 0.64 to 0.735 (vitamin D and COVID-19 0.735,
acupuncture 0.724, both terms mentioned in some articles); non-medical 0.47
to 0.56. On the evaluation database, set 1's answerable questions reach as
low as 0.73 (q08) while its should-abstain questions reach 0.79 (library
papers without stored text). **Decision: keep 0.7.** Raising it to 0.75 would
refuse q08, an answerable question, and no threshold separates the two
groups; the model's INSUFFICIENT_EVIDENCE judgement, which declined all nine
should-abstain questions in set 1, remains the main guard. Set 2's scores show
the same overlap (answerable from 0.717, should-abstain up to 0.825) but were
only reported, not used for the decision, to keep it held out.

### Minimum Python 3.11 and the MIT licence (2026-11-21)

- **Python 3.11 is the minimum** (`requires-python = ">=3.11"`), replacing
  the 3.9 compatibility target. Streamlit and FastAPI already needed 3.11, so
  every part of the project now runs on one range. Removed as 3.9-only
  workarounds: the `python_version` markers on the extras and requirement
  files, and the pattern-based reading of `pyproject.toml` in
  `tests/test_packaging.py` (now `tomllib`). Ruff targets `py311`.
  `from __future__ import annotations` stays: it is harmless on 3.11 and
  removing it from 52 files would be churn with no behaviour change.
- **CI tests 3.11 and 3.12.** The 3.12 job installs with
  `constraints-deploy.txt`, as the deployment does. The 3.11 job first runs
  the suite with the standard library only (the core must stay
  dependency-free), then installs the extras and runs it again, without the
  deploy constraints, because their numpy 2.5.5 needs Python 3.12 (pip
  resolves numpy 2.4.6 for 3.11, checked on 2026-11-21).
- **MIT licence** for the code (copyright 2026 Ronit Shah), declared in
  `pyproject.toml` as an SPDX expression (setuptools 77 or newer). It does
  not cover stored article text, which keeps its CC BY or CC0 licence and
  attribution (CONTENT_POLICY.md); the README says so.

### Small latency samples do not replace the record (2026-11-24)

- **Problem:** `hri eval` rewrote `eval/results/latency.json` from whatever
  answers a run did not find in the cache. The set 2 re-run after review had
  only 2 such answers and replaced a 29-answer measurement with them.
- **Decision:** a run with fewer than 10 uncached answers
  (`MIN_LATENCY_ANSWERS`) keeps an existing `latency.json` and appends its
  sample to `eval/results/latency-samples.jsonl`; the report shows the kept
  record and points to the log. With no record yet, the sample is written as
  before. 10 is the most `--no-cache` allows, so `--no-cache --max-questions
  10` is the way to replace the record on purpose; the default `--no-cache`
  run (5 questions) is now logged only.
- **Not done:** combining samples into one figure; samples from different
  question sets and days are not comparable enough.

## Unresolved future decisions

These items are not approved or implemented changes.

- Whether unknown keys should be rejected, logged, retained, or continue to be
  ignored.
- Whether source identifiers need a separate source type (for example, PMID vs
  DOI) and source-specific validation in a later ingestion stage.
- Whether source URLs need stricter syntax validation, allowed domains, or a
  later reachability check subject to source terms and rate limits.
- Whether arbitrary author iterables should be accepted.
- Which additional metadata fields (journal, publication date, licence,
  language, source provider, full-text availability) belong in a later scope.
- Which integration-hardening capability should follow Milestone 2B.
