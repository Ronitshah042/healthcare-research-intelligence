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
