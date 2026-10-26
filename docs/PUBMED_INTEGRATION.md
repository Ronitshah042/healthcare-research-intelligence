# PubMed Integration

## Scope

Milestone 2A provides a framework-neutral Python client for searching PubMed
citations through the official NCBI E-utilities API. It retrieves bibliographic
metadata only. It does not retrieve PMC or publisher full text, display
abstracts, cache abstracts, persist any API response, or make clinical claims.

## Configuration

`PubMedClientConfig` is kept separate from the client implementation. A
production client must have a project-owned application name and developer
contact configured before it makes requests:

| Environment variable | Purpose |
| --- | --- |
| `PUBMED_NCBI_TOOL` | Registered, no-whitespace application identifier. |
| `PUBMED_NCBI_EMAIL` | Developer contact email; never an end-user address. |
| `PUBMED_NCBI_API_KEY` | Optional NCBI API key. Do not commit it. |
| `PUBMED_TIMEOUT_SECONDS` | Optional positive request timeout in seconds. |
| `PUBMED_REQUESTS_PER_SECOND` | Optional request rate at or below NCBI's limit. |
| `PUBMED_EUTILITIES_BASE_URL` | Optional HTTPS URL on the official NCBI E-utilities host. |

The default endpoint is `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`.
The client rejects `http` URLs and any other host, including lookalike hosts.
This prevents the configured tool name, developer email, or API key from being
sent to an endpoint outside the official NCBI E-utilities host. The endpoint
override is therefore not suitable for local proxies or mock servers; tests
inject a transport instead.
Without an API key, the client limits its own requests to three per second; an
API key allows a configured rate up to ten per second. The limiter is per
client process, so a future multi-process deployment needs a shared limiter.
Timeouts and configured request rates must be finite positive numbers; `NaN`
and infinity are rejected.
The client does not retry failures automatically in this milestone.

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

Whitespace-only input returns an empty result without an HTTP request. The
input is URL encoded, and the service uses ESearch with `usehistory=y` then a
single batched EFetch request for the requested page. Results expose the total
ESearch count and page data. Page numbering begins at one, and page sizes from
one through 20 are accepted; larger sizes are rejected rather than silently
changed.

Title, title/abstract, and author methods accept plain-text terms, not general
PubMed query expressions. They normalise ordinary whitespace but reject square
brackets, parentheses, double quotes, and standalone uppercase `AND`, `OR`, or
`NOT` operators. This prevents caller input from changing the intended field.
Common literal punctuation such as hyphens, apostrophes, commas, colons, and
slashes remains accepted. `lookup_doi` accepts a trimmed DOI beginning with
`10.` followed by a four-to-nine-digit registrant code and `/`, with no
whitespace; it is sent as a quoted `[aid]` value. `lookup_pmid` accepts trimmed
ASCII digits only. The client deliberately does not provide an advanced-query
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
misreporting it as a successful empty page.

## Content rights and presentation requirements

The client always maps `PaperMetadata.abstract` to `None`, even when PubMed
returns `AbstractText`. No full text is fetched. A future change to display,
cache, or persist abstracts requires a separate rights review and explicit
approval; NLM does not claim copyright in PubMed abstracts, but publishers or
authors may hold rights.

A future UI must make the following visible with PubMed-derived results:

- `Source: PubMed, U.S. National Library of Medicine (NLM), NIH.`
- The [NCBI policies and disclaimer](https://www.ncbi.nlm.nih.gov/home/about/policies/).
- `This independent service is not affiliated with, endorsed by, or sponsored
  by NCBI, NLM, NIH, or HHS.`

The independently branded application must not imply government endorsement or
use NCBI/NLM branding as its own. Before public launch, obtain legal review of
the final attribution/disclaimer presentation and any planned reuse or
retention of publisher-supplied material.

## Testing

Tests inject a transport, clock, and sleeper. They use static XML responses and
never make live PubMed requests. Coverage includes all search modes, fallback,
year filters, pagination, XML mapping, missing abstracts, malformed records,
timeouts, HTTP and transport errors, malformed XML, configuration, and rate
limiting. The rate-limit scheduling step is locked for a shared client; the
lock is released before the HTTP transport begins.
