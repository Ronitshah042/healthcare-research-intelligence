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

## Unresolved future decisions

These items are not approved or implemented changes.

- Whether unknown keys should be rejected, logged, retained, or continue to be
  ignored.
- Whether source identifiers need a separate source type (for example, PMID vs
  DOI) and source-specific validation in a later ingestion stage.
- Whether source URLs need stricter syntax validation, allowed domains, or a
  later reachability check subject to source terms and rate limits.
- Whether arbitrary author iterables should be accepted.
- Whether an explicit project Python-version requirement and packaging
  configuration should be added.
- Which additional metadata fields (journal, publication date, licence,
  language, source provider, full-text availability) belong in a later scope.
- Which integration-hardening capability should follow Milestone 2B.
