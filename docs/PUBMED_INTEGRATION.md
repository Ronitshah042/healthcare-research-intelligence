# PubMed Integration

## Scope

Milestone 2A provides a framework-neutral Python client for searching PubMed
citations through the official NCBI E-utilities API. Milestone 2B hardens that
client's process-local request coordination and transient-failure handling. The
client retrieves bibliographic metadata only. It does not retrieve PMC or
publisher full text, display abstracts, cache abstracts, persist any API
response, or make clinical claims.

## Milestone 2B acceptance criteria

Milestone 2B is complete when all of the following are true:

- Multiple client instances in one process share a thread-safe rate schedule
  for the same authentication scope, without holding a lock during transport.
- Transient failures have bounded, configured retries; every retry passes
  through the rate limiter.
- Well-formed NCBI XML error payloads raise `PubMedResponseError` without
  including configured secrets in the exception message.
- Mocked tests cover shared scheduling, retry policy, `Retry-After`,
  non-retryable failures, and ESearch/EFetch XML errors.

Excluded from 2B are cross-process/distributed rate limiting, live integration
tests, persistent retry queues, circuit breakers, and full-text retrieval.

## Configuration

`PubMedClientConfig` is kept separate from the client implementation. A
production client must have a project-owned application name and developer
contact configured before it makes requests:

| Environment variable | Purpose |
| --- | --- |
| `PUBMED_NCBI_TOOL` | Registered, no-whitespace application identifier. |
| `PUBMED_NCBI_EMAIL` | Developer contact email; never an end-user address. |
| `PUBMED_NCBI_API_KEY` | Optional NCBI API key; a blank value means no key. Do not commit it. |
| `PUBMED_TIMEOUT_SECONDS` | Optional positive request timeout in seconds. |
| `PUBMED_REQUESTS_PER_SECOND` | Optional request rate at or below NCBI's limit. |
| `PUBMED_MAX_RETRIES` | Optional non-negative number of retries; default `2`. |
| `PUBMED_RETRY_BACKOFF_SECONDS` | Optional positive exponential-backoff base; default `0.5`. |
| `PUBMED_EUTILITIES_BASE_URL` | Optional HTTPS URL on the official NCBI E-utilities host, with no port, user info, query, or fragment. |

The default endpoint is `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`.
The client rejects `http` URLs and any other host, including lookalike hosts.
This prevents the configured tool name, developer email, or API key from being
sent to an endpoint outside the official NCBI E-utilities host. The endpoint
override is therefore not suitable for local proxies or mock servers; tests
inject a transport instead.
Without an API key, clients share an anonymous process-local schedule capped at
three requests per second. Clients that use the same API key share a separate
process-local schedule capped at ten requests per second; the in-memory
scheduler stores only a SHA-256 digest of the key. Different API keys use
different schedules. A lower configured rate is honoured when that client
reserves a slot. The scheduler lock protects slot reservation only, never the
HTTP transport. A future multi-process deployment still needs a distributed
limiter.
Timeouts and configured request rates must be finite positive numbers; `NaN`
and infinity are rejected.

## Retry policy

The client makes at most three attempts by default: one initial request plus
two retries. It waits `0.5`, then `1.0` seconds by default, doubling the base
backoff for each retry. The following failures are retried: timeouts, URL and
other transport errors, and HTTP 408, 429, 500, 502, 503, and 504 responses.
For a numeric `Retry-After` response header, the client waits at least that
many seconds. Invalid user input, configuration failures, malformed XML, NCBI
XML error payloads, TLS certificate verification failures, and other HTTP
statuses are not retried. Each new attempt
reserves a fresh process-shared rate-limit slot.

NCBI requires E-utilities users to identify distributed software with `tool`
and developer `email`, and its guidance sets the corresponding request-rate
limits. The values must be registered with NCBI before production use; merely
including them in requests is not the whole registration process.

## Search contract

`PubMedClient` provides five focused methods:

| Method | PubMed field | Fallback |
| --- | --- | --- |
| `search_title(...)` | `[ti]` | One `[tiab]` search only when `[ti]` has zero matches. |
| `search_title_abstract(...)` | `[tiab]` | None. |
| `search_author(...)` | `[au]` | None. |
| `lookup_doi(...)` | `[aid]` | None. |
| `lookup_pmid(...)` | `[pmid]` | None. |

Title and title/abstract searches require **every word** in the field: the
term is `word1[tiab] AND word2[tiab] ...` (edge commas, semicolons and colons
are dropped from each word). Author searches keep the name together as a
phrase, `"Polack FP"[au]`; PMID lookups are `123[pmid]`. Until 2026-11-20 the
term was `(word1 word2 ...)[tiab]`, which PubMed reads as the first word in
all fields plus the remaining words as one exact phrase, so most multi-word
searches returned too few or no results.

Whitespace-only input returns an empty result without an HTTP request. The
input is URL encoded, and the service uses ESearch with `usehistory=y` then a
single batched EFetch request for the requested page. Results expose the total
ESearch count and page data. Page numbering begins at one, and page sizes from
one through 20 are accepted; larger sizes are rejected rather than silently
changed.

Title, title/abstract, and author methods accept plain-text terms, not general
PubMed query expressions. They normalise ordinary whitespace but reject square
brackets, parentheses, double quotes, `*` truncation, `#` history references,
and standalone uppercase `AND`, `OR`, or
`NOT` operators. This prevents caller input from changing the intended field.
Common literal punctuation such as hyphens, apostrophes, commas, colons, and
slashes remains accepted. `lookup_doi` accepts a trimmed DOI beginning with
`10.` followed by a four-to-nine-digit registrant code and `/`, with no
whitespace; a leading `doi:` or DOI URL prefix is removed first, and the DOI is
sent as a quoted `[aid]` value. `lookup_pmid` accepts trimmed ASCII digits
only and ignores leading zeros. The client deliberately does not provide an advanced-query
method in this milestone.

The optional `publication_year` and `publication_year_end` create an inclusive
PubMed `[pdat]` filter. The same filter is sent in the initial title search and
in its title-to-abstract fallback.

## Citation mapping

Each valid result contains a `PubMedSearchRecord` around the existing frozen
`PaperMetadata` model. `PaperMetadata.source_identifier` is `PMID:<id>` and
`source_url` is the canonical `https://pubmed.ncbi.nlm.nih.gov/<id>/` record
link. DOI, journal title, raw citation-date text, and request metadata stay in
the PubMed-specific result model.

### Primary publication year

`PaperMetadata.publication_year` is the primary journal-citation year, not
necessarily the earliest electronic-publication year. The deterministic order
is:

1. Use `MedlineCitation/Article/Journal/JournalIssue/PubDate/Year` when it is
   present.
2. Only when `Year` is absent, inspect `MedlineDate` and take the first ASCII
   four-digit year in document order. For example, `1998 Dec-1999 Jan` becomes
   `1998`.
3. Do not substitute `ArticleDate` or publication-history dates.
4. A present but malformed `Year`, or no usable primary citation date, makes
   the citation invalid for the current `PaperMetadata` model. It is skipped
   and counted in `invalid_record_count`.
5. The extracted value is passed through `normalise_paper_metadata`, so the
   existing year validation remains authoritative.

Two record shapes are handled specially:

- **Books and book chapters** arrive as `PubmedBookArticle` and are not mapped.
  Each one is counted in `invalid_record_count`, so a page can hold fewer
  records than requested; a page of only books is an empty page, not an error.
- **Citations without listed authors** are valid and have an empty author
  tuple.

Author names are stored as `ForeName LastName` (for example
`Fernando P Polack`); `Initials` are used only when `ForeName` is missing, and
a `CollectiveName` is kept unchanged.

Each record also carries `CitationDetails`, the extra bibliographic fields a
formal citation needs. It is citation metadata only:

| Field | PubMed XML source |
| --- | --- |
| `authors` | `LastName` then `Initials` (for example `Polack FP`); `CollectiveName` unchanged |
| `journal_abbreviation` | `MedlineJournalInfo/MedlineTA`, else `Journal/ISOAbbreviation` without full stops |
| `volume`, `issue` | `Journal/JournalIssue/Volume`, `Issue` |
| `pages` | `Article/Pagination/MedlinePgn` |

The `[pdat]` filter uses PubMed's publication-date index, while the returned
model uses the journal citation rule above. Online-first and print dates can
differ, so a `[pdat]` result is not a guarantee that the displayed primary
citation year is inside every requested range.

## Errors and malformed records

Configuration issues raise `PubMedConfigurationError`; invalid local requests
raise `PubMedRequestValidationError`. Request-level timeouts, HTTP failures,
network failures, malformed XML, and API error payloads raise specific
`PubMedError` subclasses. A malformed individual citation is skipped while
other records in the EFetch batch are retained, and the result reports the
skipped-record count. If a positive ESearch result leads to a well-formed
EFetch document with no `PubmedArticle` elements, including an empty
`PubmedArticleSet`, the client raises `PubMedResponseError` rather than
misreporting it as a successful empty page. NCBI XML error text is not copied
into the exception message, because an upstream error can echo request values
such as an API key.

## Content rights and presentation requirements

The client always maps `PaperMetadata.abstract` to `None`, even when PubMed
returns `AbstractText`. It separately records whether an `AbstractText`
element was present, so downstream metadata can accurately report abstract
availability without retaining its contents. No full text is fetched. A future
change to display, cache, or persist abstracts requires a separate rights
review and explicit approval; NLM does not claim copyright in PubMed
abstracts, but publishers or authors may hold rights.

When using the ingestion search pipeline, `SearchResult.total_count` is the
full number of matches reported by PubMed, not the number returned on one
page. `SearchResult.normalised_count` is the number of valid, deduplicated
records returned for that current page. The values can differ because of
pagination, invalid source records, and identifier-based deduplication.

A future UI must make the following visible with PubMed-derived results:

- `Source: PubMed, U.S. National Library of Medicine (NLM), NIH.`
- The [NCBI policies and disclaimer](https://www.ncbi.nlm.nih.gov/home/about/policies/).
- `This independent service is not affiliated with, endorsed by, or sponsored
  by NCBI, NLM, NIH, or HHS.`

### Where code finds the attribution (contract for a future UI)

Every PubMed-derived result exposes the same `PUBMED_ATTRIBUTION` object, with
`source_name`, `source_url`, `disclaimer_url`, and
`independent_service_notice` text fields:

| Result | How to read it |
| --- | --- |
| `PubMedSearchPage` (client) | `page.attribution`, always set, including empty pages |
| `SearchResult` (pipeline) | `result.attribution`, always set; each record has `source_name == "PubMed"` |
| `StoredPaper` (`PaperStore.save`, `get`, `list_papers`, `search`) | `paper.attribution`: `PUBMED_ATTRIBUTION` when `paper.source_name == "PubMed"`, otherwise `None` |

A presentation layer must display the attribution whenever it is not `None`.
`PaperStore` records `source_name` per paper. A database created before this
column existed is upgraded when opened and its rows are marked as PubMed. An
update that carries no source keeps the source already recorded.

The independently branded application must not imply government endorsement or
use NCBI/NLM branding as its own. Before public launch, obtain legal review of
the final attribution/disclaimer presentation and any planned reuse or
retention of publisher-supplied material.

## Testing

Tests inject a transport, clock, and sleeper. They use static XML responses and
never make live PubMed requests. Coverage includes all search modes, fallback,
year filters, pagination, XML mapping, missing abstracts, malformed records,
timeouts, HTTP and transport errors, malformed XML, configuration, and rate
limiting. Milestone 2B adds deterministic tests for shared multi-client,
multi-thread scheduling; transient retries; `Retry-After`; permanent and XML
failure exclusions; and safe ESearch/EFetch XML error translation.
