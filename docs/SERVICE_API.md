# Service API

This is the contract between the application logic and anything that presents
it: the `hri` command line today, a UI or web API later. Everything is in one
module:

```python
from hri import services
```

## Rules that apply to every function

- **Return values are typed objects** (frozen dataclasses or tuples of them).
  Nothing is printed and no text is formatted for display.
- **Only `ingest`, `fetch_fulltext`, and `recheck_fulltext` contact the network.**
- **`database_path`** is an optional keyword on every function. When omitted,
  the path is `HRI_DB_PATH` if set, otherwise `data/hri.sqlite3` relative to
  the current working directory. The database and its folder are created on
  first use and upgraded automatically if they come from an older version.
- **Calls are independent.** Each one opens and closes its own database
  connection, so functions can be called from different threads or requests.
- **Attribution must be displayed.** Wherever a result has an `attribution`
  that is not `None`, a UI must show its four text fields alongside the data
  (see [Attribution](#attribution)).
- **PubMed abstracts are never available.** Only the `abstract_available` flag
  is stored on a paper; link to PubMed for the abstract itself. The separate
  full-text store holds open-access articles allowed by
  [CONTENT_POLICY.md](CONTENT_POLICY.md), whose own abstract is one of their
  sections. Never show an article's text without its `attribution`.

## Quick reference

| Function | Purpose | Returns |
| --- | --- | --- |
| [`ingest`](#ingest) | Fetch PubMed citations and save them | `IngestResult` |
| [`search_papers`](#search_papers) | Search stored papers | `SearchResults` |
| [`get_paper`](#get_paper) | One stored paper by PMID or DOI | `StoredPaper` |
| [`get_stats`](#get_stats) | Counts of stored papers | `PaperStoreStats` |
| [`create_list`](#create_list) | Create an empty saved list | `SavedList` |
| [`rename_list`](#rename_list) | Rename a saved list | `SavedList` |
| [`delete_list`](#delete_list) | Delete a saved list (not its papers) | `SavedList` |
| [`add_to_list`](#add_to_list) | Add a stored paper to a list | `ListChange` |
| [`remove_from_list`](#remove_from_list) | Remove a paper from a list | `ListChange` |
| [`get_lists`](#get_lists) | All saved lists | `tuple[SavedList, ...]` |
| [`get_list`](#get_list) | One list with its papers | `ListContents` |
| [`export_list`](#export_list) | A list as CSV, Vancouver citations, or plain text | `ListExport` |
| [`fetch_fulltext`](#fetch_fulltext) | Store allowed open-access full text | `FullTextFetchResult` |
| [`recheck_fulltext`](#recheck_fulltext) | Remove stored articles that are now retracted | `FullTextRecheckResult` |
| [`get_fulltext_stats`](#get_fulltext_stats) | Counts of stored full text and rejections | `FullTextStats` |
| [`search_passages`](#search_passages) | Keyword search of stored full-text passages | `tuple[PassageMatch, ...]` |

## Errors

All are importable from `hri.services`.

| Error | Meaning | Raised by |
| --- | --- | --- |
| `InvalidRequestError` | An argument is unusable: blank query, unknown mode or strategy, bad page or year, malformed Boolean query, malformed PMID/DOI, bad list name, unknown export format. Subclass of `ValueError`. The message is safe to show to a user. | every function that takes arguments |
| `PaperNotFoundError` | No stored paper has that PMID or DOI. | `get_paper`, `add_to_list` |
| `SavedListNotFoundError` | No saved list has that name. | `rename_list`, `delete_list`, `add_to_list`, `remove_from_list`, `get_list`, `export_list` |
| `SavedListExistsError` | A list with that name already exists (names ignore case). | `create_list`, `rename_list` |
| `PaperStorageError` | The database could not be read or written, or was created by a newer version of this project. | every function |
| `PubMedConfigurationError` | `PUBMED_NCBI_TOOL` or `PUBMED_NCBI_EMAIL` is missing or invalid. Subclass of `ValueError`, but **not** of `InvalidRequestError`. | `ingest` |
| `PubMedError` | PubMed or PMC could not be reached or returned something unusable. PMC failures are the `PmcError` subclasses `PmcTimeoutError`, `PmcTransportError`, `PmcHttpError`, and `PmcResponseError`. Subclasses: `PubMedTimeoutError`, `PubMedTransportError`, `PubMedHttpError` (has `.status_code`), `PubMedResponseError`. Messages never contain the API key, request URL, or NCBI error text. | `ingest`, `fetch_fulltext`, `recheck_fulltext` |
| `LicencePolicyError` | `HRI_ALLOWED_LICENCES` is set but lists no licence. A configuration problem, not user input. | `fetch_fulltext` |

**Catch order matters.** `PaperNotFoundError`, `SavedListNotFoundError`, and
`SavedListExistsError` are subclasses of `PaperStorageError`. Catch the specific
error first:

```python
try:
    paper = services.get_paper(identifier)
except services.InvalidRequestError as error:   # show the message, ask again
    ...
except services.PaperNotFoundError:             # "not in your library"
    ...
except services.PaperStorageError:              # database problem
    ...
```

## Result types

### `StoredPaper`

One citation from the local database. Returned inside most results.

| Field | Type | Notes |
| --- | --- | --- |
| `pmid` | `str \| None` | Digits only, no leading zeros. |
| `doi` | `str \| None` | Lower case, no `doi:` or URL prefix. At least one of `pmid` and `doi` is set. |
| `title` | `str` | |
| `authors` | `tuple[str, ...]` | In citation order. **May be empty.** |
| `journal` | `str \| None` | |
| `publication_year` | `int \| None` | The journal citation year. |
| `abstract_available` | `bool` | Whether PubMed has an abstract; the text is not stored. |
| `source_identifier` | `str \| None` | For example `PMID:12345`. |
| `source_url` | `str \| None` | URL recorded at ingest. |
| `source_name` | `str \| None` | `"PubMed"` for everything `ingest` saves. |
| `citation` | `CitationDetails \| None` | Extra fields for formal citations; see below. `None` for papers saved before these were recorded. |
| `attribution` (property) | `PubMedAttribution \| None` | Set when `source_name` is `"PubMed"`. |
| `pubmed_url` (property) | `str \| None` | `https://pubmed.ncbi.nlm.nih.gov/<pmid>/` for PubMed papers with a PMID. Use this for the "view on PubMed" link. |

### `CitationDetails`

Bibliographic fields used by the Vancouver export. Any of them may be missing.

| Field | Type | Notes |
| --- | --- | --- |
| `authors` | `tuple[str, ...]` | NLM form, such as `Polack FP`; group names unchanged. Same order as `StoredPaper.authors`. |
| `journal_abbreviation` | `str \| None` | NLM abbreviation, such as `N Engl J Med`. |
| `volume`, `issue`, `pages` | `str \| None` | As PubMed gives them, for example `383`, `27`, `2603-2615`. |

### Attribution

`PubMedAttribution` has four text fields: `source_name`, `source_url`,
`disclaimer_url`, and `independent_service_notice`. Read it from the result you
are displaying instead of hard-coding the text. Result-level `attribution` fields are a convenience: they are set when at least
one paper in the result is PubMed-derived, so a page needs to show it only once.

### `SavedList`

| Field | Type | Notes |
| --- | --- | --- |
| `name` | `str` | As first created; surrounding and repeated spaces removed. |
| `paper_count` | `int` | |

### `PaperStoreStats`

| Field | Type | Notes |
| --- | --- | --- |
| `total_papers` | `int` | |
| `papers_by_year` | `tuple[tuple[int \| None, int], ...]` | `(year, count)` pairs, oldest first. A year of `None` means unknown and comes last. Years with no papers are absent. |
| `with_doi`, `without_doi` | `int` | Sum to `total_papers`. |
| `abstract_available`, `abstract_unavailable` | `int` | Sum to `total_papers`. |

## Constants

Use these to build menus and validate input instead of repeating the values.

| Name | Value |
| --- | --- |
| `SearchMode` | Enum of ingest modes: `TITLE`, `TITLE_ABSTRACT`, `AUTHOR`, `DOI`, `PMID`; each `.value` is the lower-case text form. |
| `SEARCH_STRATEGIES` | `("keyword", "fuzzy", "boolean")` |
| `EXPORT_FORMATS` | `("csv", "citations", "plain")` |
| `DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE` | `20`, `100` |
| `DEFAULT_INGEST_RECORDS`, `MAX_INGEST_RECORDS` | `100`, `1000` |
| `DEFAULT_FULLTEXT_ARTICLES`, `MAX_FULLTEXT_ARTICLES` | `20`, `200` |
| `DEFAULT_PASSAGE_RESULTS`, `MAX_PASSAGE_RESULTS` | `10`, `50` |
| `SECTION_NAMES` | `("title", "abstract", "introduction", "methods", "results", "discussion", "conclusion", "other")` |

## Functions

### `ingest`

```python
ingest(query, *, mode=SearchMode.TITLE_ABSTRACT, max_records=100,
       year_from=None, year_to=None, database_path=None, client=None)
    -> IngestResult
```

Fetches citations from PubMed and saves them. This is the only function that
uses the network, and it can take a while: PubMed returns 20 records per
page, each page needs two requests, and requests are rate limited (3 per
second without an API key, 10 with one). A UI should run it in the background.

| Input | Type | Rules |
| --- | --- | --- |
| `query` | `str` | Non-empty. For `title`, `title_abstract`, and `author` it is plain text: `[ ] ( ) " * #` and upper-case `AND`/`OR`/`NOT` are rejected. For `doi` and `pmid` it is one identifier. |
| `mode` | `SearchMode` or `str` | `"title"`, `"title_abstract"`, `"author"`, `"doi"`, `"pmid"`. `title` falls back to title/abstract once if nothing matches. |
| `max_records` | `int` | 1 to 1000 (`services.MAX_INGEST_RECORDS`). At most this many PubMed positions are read. |
| `year_from`, `year_to` | `int \| None` | Inclusive. Either may be given alone: `year_from` alone means "to now", `year_to` alone means "up to that year". |
| `client` | `PubMedClient \| None` | Leave as `None`; the client is built from the `PUBMED_*` environment variables. Tests pass a fake. |

`IngestResult`:

| Field | Type | Meaning |
| --- | --- | --- |
| `query`, `mode`, `requested_max` | | The request, with `mode` as a `SearchMode`. |
| `total_available` | `int` | PubMed's total match count for the query. |
| `fetched` | `int` | Valid records received, at most `requested_max`. |
| `saved` | `int` | Distinct papers inserted or updated. |
| `duplicates` | `int` | Repeats of a paper already handled in this run. |
| `invalid_skipped` | `int` | Records that could not be used, including books and book chapters. |
| `conflicts` | `int` | Records skipped because their PMID and DOI point at two different stored papers. |
| `fallback_used` | `bool` | `True` when a title search fell back to title/abstract. |
| `attribution` | `PubMedAttribution` | Always set. |

`fetched` equals `saved` plus `conflicts` plus the cross-page part of
`duplicates`. Saving a paper that is already stored updates it and counts as
saved.

Errors: `InvalidRequestError`, `PubMedConfigurationError`, `PubMedError`,
`PaperStorageError`. If PubMed fails partway through, the error is raised and
the papers saved before it stay saved.

### `search_papers`

```python
search_papers(query, *, strategy="keyword", page=1, page_size=20,
              year_from=None, year_to=None, abstract_available=None,
              database_path=None) -> SearchResults
```

Searches titles, author names, and journal names of stored papers.

| Input | Type | Rules |
| --- | --- | --- |
| `query` | `str` | Non-empty. |
| `strategy` | `str` | `"keyword"`: every word must appear. `"fuzzy"`: as keyword, plus titles with small spelling differences, ranked after exact matches. `"boolean"`: `AND`, `OR`, `NOT` (any case), parentheses, and `"quoted phrases"`; operators must be written out, so `sleep diet` is an error but `sleep AND diet` is not. |
| `page` | `int` | 1 or more. A page past the end returns no papers. |
| `page_size` | `int` | 1 to 100 (`services.MAX_PAGE_SIZE`). |
| `year_from`, `year_to` | `int \| None` | Inclusive; papers with an unknown year are excluded when either is set. |
| `abstract_available` | `bool \| None` | `True` or `False` to filter; `None` for both. |

`SearchResults`:

| Field | Type | Meaning |
| --- | --- | --- |
| `query`, `strategy`, `page`, `page_size` | | The request. |
| `papers` | `tuple[StoredPaper, ...]` | This page, best match first. Title matches outrank author matches, which outrank journal matches; ties keep the order papers were first saved. |
| `total_count` | `int` | Matches across all pages. |
| `total_pages` | `int` | `0` when nothing matches. |
| `attribution` | `PubMedAttribution \| None` | Set when any paper on the page is PubMed-derived. |

Errors: `InvalidRequestError`, `PaperStorageError`.

### `get_paper`

```python
get_paper(identifier, *, database_path=None) -> StoredPaper
```

`identifier` is a PMID (digits; leading zeros ignored) or a DOI (with or
without a `doi:` or `https://doi.org/` prefix; case ignored).

Errors: `InvalidRequestError` (blank or malformed identifier),
`PaperNotFoundError`, `PaperStorageError`.

### `get_stats`

```python
get_stats(*, database_path=None) -> PaperStoreStats
```

Errors: `PaperStorageError`.

### `create_list`

```python
create_list(name, *, database_path=None) -> SavedList
```

`name` must be 1 to 100 characters after trimming. Names are unique ignoring
case.

Errors: `InvalidRequestError`, `SavedListExistsError`, `PaperStorageError`.

### `rename_list`

```python
rename_list(name, new_name, *, database_path=None) -> SavedList
```

Renames a list and keeps its papers and position. `new_name` follows the
`create_list` rules and must not belong to another list. Changing only the
letter case of a name is allowed. Returns the list under its new name.

Errors: `InvalidRequestError`, `SavedListNotFoundError`,
`SavedListExistsError`, `PaperStorageError`.

### `delete_list`

```python
delete_list(name, *, database_path=None) -> SavedList
```

Deletes the list permanently. The papers stay in the database and in other
lists. Returns the list as it was just before deletion (its name and paper
count), so a UI can confirm what was removed. There is no undo; ask the user to
confirm before calling it.

Errors: `InvalidRequestError`, `SavedListNotFoundError`, `PaperStorageError`.

### `add_to_list`

```python
add_to_list(name, identifier, *, database_path=None) -> ListChange
```

Adds a paper that is already stored. `identifier` follows the `get_paper`
rules; `name` ignores case.

`ListChange`:

| Field | Type | Meaning |
| --- | --- | --- |
| `saved_list` | `SavedList` | The list after the change, with its new count. |
| `paper` | `StoredPaper \| None` | The paper concerned. |
| `changed` | `bool` | `False` when the list already contained the paper; nothing is duplicated. |

Errors: `InvalidRequestError`, `SavedListNotFoundError`, `PaperNotFoundError`,
`PaperStorageError`.

### `remove_from_list`

```python
remove_from_list(name, identifier, *, database_path=None) -> ListChange
```

Removes the paper from the list only; the paper stays in the database and in
other lists. `changed` is `False` when the list did not contain it. `paper` is
`None` when no stored paper has that identifier (this is not an error).

Errors: `InvalidRequestError`, `SavedListNotFoundError`, `PaperStorageError`.

### `get_lists`

```python
get_lists(*, database_path=None) -> tuple[SavedList, ...]
```

Every list, in the order created. Empty tuple when there are none.

Errors: `PaperStorageError`.

### `get_list`

```python
get_list(name, *, database_path=None) -> ListContents
```

`ListContents` has `saved_list` (`SavedList`), `papers`
(`tuple[StoredPaper, ...]`, in the order added), and `attribution`
(`PubMedAttribution | None`). There is no paging.

Errors: `InvalidRequestError`, `SavedListNotFoundError`, `PaperStorageError`.

### `export_list`

```python
export_list(name, export_format="csv", *, database_path=None) -> ListExport
```

Returns text; it does not write a file. The caller decides whether to save or
download it.

`ListExport`:

| Field | Type | Meaning |
| --- | --- | --- |
| `list_name` | `str` | |
| `export_format` | `str` | One of `services.EXPORT_FORMATS`: `"csv"`, `"citations"`, or `"plain"`. |
| `content` | `str` | The document, UTF-8 text with `\n` line endings. |
| `paper_count` | `int` | |
| `attribution` | `PubMedAttribution \| None` | |

- **`csv`:** a header row, then one row per paper. Columns: `pmid`, `doi`,
  `title`, `authors` (full names joined with `; `), `journal`,
  `publication_year`, `volume`, `issue`, `pages`, `abstract_available`
  (`yes`/`no`), `source`, `url`. A cell that starts with `=`, `+`, `-`, or `@`
  is prefixed with `'` so spreadsheet programs do not run it as a formula. The
  attribution is **not** inside the CSV, so display `ListExport.attribution`
  wherever the file is offered. An empty list gives the header row only.
- **`citations`:** Vancouver (NLM) style, one numbered line per paper,
  followed by a blank line and the attribution text:

  ```text
  1. Polack FP, Thomas SJ, Kitchin N, Absalon J, Gurtman A, Lockhart S, et al. Safety and Efficacy of the BNT162b2 mRNA Covid-19 Vaccine. N Engl J Med. 2020;383(27):2603-2615. doi: 10.1056/nejmoa2034577. PMID: 33301246.
  ```

  The first six authors are listed, then `et al`. Parts that are not stored
  are left out. It differs from a PubMed-generated citation in three ways: the
  date is the year only, DOIs are lower case, and a paper saved before
  citation details were recorded uses full author names and the full journal
  title. Books are not stored, so only journal articles are covered.
- **`plain`:** a simple readable layout, not a formal style:
  `Authors. Title. Journal. Year. PMID: n. doi:x. link`, numbered, followed by
  the attribution text.
- An empty list gives an empty string for `citations` and `plain`.

Errors: `InvalidRequestError` (unknown format or bad name),
`SavedListNotFoundError`, `PaperStorageError`.

### `fetch_fulltext`

```python
fetch_fulltext(*, from_db=False, query=None, max_articles=20,
               database_path=None, client=None, policy=None, now=None)
    -> FullTextFetchResult
```

Stores the full text of PMC Open Access articles allowed by the content
policy. Give exactly one source:

- `from_db=True`: articles for papers already stored, matched by PMID, or by
  DOI for papers without a PMID.
- `query="..."`: plain-text search of PMC limited to open-access articles
  (the same syntax rules as `ingest`). Up to 200 results are considered, so
  repeated runs move past articles already stored.

For each article the **current version** is looked up, then its licence and
status are read **before** any text is downloaded. Retracted articles,
disallowed or missing licences, and articles not in the Open Access subset are
rejected: nothing is stored for them and the rejection is recorded. Allowed
articles are parsed into sections, split into passages, and stored with their
licence and attribution. Like `ingest`, it can take a while; run it in the
background.

| Input | Type | Rules |
| --- | --- | --- |
| `from_db`, `query` | `bool`, `str \| None` | Exactly one source. |
| `max_articles` | `int` | 1 to 200. Counts articles processed in this run; articles whose current version is already stored are skipped without counting. |
| `client`, `policy`, `now` | | Leave as `None`. The client and licence allowlist come from the environment; tests pass fakes. |

`FullTextFetchResult`:

| Field | Type | Meaning |
| --- | --- | --- |
| `source`, `query` | `str`, `str \| None` | `"database"` or `"query"`, and the query if any. |
| `candidates` | `int` | Articles with a PMC record that were considered. |
| `stored` | `int` | Articles stored or updated, including flagged ones. |
| `already_stored` | `int` | Current version already stored; nothing fetched. |
| `rejected_licence` | `int` | Licence missing or not allowed. |
| `rejected_retracted` | `int` | Retracted. |
| `not_in_open_access` | `int` | Not in the Open Access subset, including the next field. |
| `current_version_unavailable` | `int` | The current version is not in the Open Access bucket. |
| `not_in_pmc` | `int` | Stored papers with no PMC article (`from_db` only). |
| `failed` | `int` | Allowed articles whose XML could not be parsed; nothing stored. |
| `flagged` | `int` | Stored articles carrying correction or concern flags. |
| `rejections` | `tuple[FullTextRejection, ...]` | `pmcid`, `pmcid_version`, `licence`, `reason`, `recorded_at` for each rejection in this run. Never contains text. |

Errors: `InvalidRequestError`, `LicencePolicyError`,
`PubMedConfigurationError`, `PubMedError` (PMC failures), `PaperStorageError`.
If PMC fails partway through, articles stored before the failure stay stored.

### `recheck_fulltext`

```python
recheck_fulltext(*, database_path=None, client=None, now=None)
    -> FullTextRecheckResult
```

Re-reads the status of every stored article's version, and of its current
version if that has changed, and **deletes any article that is now
retracted**, recording the rejection. **Run it before every rebuild of a data
bundle.**

`FullTextRecheckResult` has `checked` (`int`), and three tuples of PMCIDs:
`removed_retracted`, `newer_version_available` (not fetched automatically;
run `fetch_fulltext` again), and `unavailable` (the stored version has left
the Cloud Service; its text is kept, because the licence it was stored under
still applies).

Errors: `PubMedConfigurationError`, `PubMedError`, `PaperStorageError`.

### `get_fulltext_stats`

```python
get_fulltext_stats(*, database_path=None) -> FullTextStats
```

`FullTextStats` has `documents`, `chunks`, `flagged_documents` (all `int`),
and `rejections_by_reason`, a tuple of `(reason, count)` pairs in alphabetical
order. Reasons are `current_version_unavailable`, `licence`,
`not_open_access`, and `retracted`.

Errors: `PaperStorageError`.

### `FullTextDocument` and `FullTextRejection`

`FullTextDocument` describes one stored article: `pmcid`, `pmcid_version`,
`pmid`, `doi`, `title`, `licence`, `source_url`, `retrieved_at`,
`attribution`, `article_type`, and `notice_flags` (for example
`("corrected",)`; empty when none are known). `FullTextRejection` is described
under `fetch_fulltext`.

### `search_passages`

```python
search_passages(
    query, *, limit=10, sections=(), database_path=None
) -> tuple[PassageMatch, ...]
```

Keyword search over the stored open-access passages, best match first.

- A passage matches when it contains every word of `query`. Words are compared
  ignoring case and accents, after English (Porter) stemming, so `veteran`
  also finds `veterans`.
- `query` is always plain words. Quotes, `*`, `:`, brackets and words such as
  `OR`, `NOT` or `NEAR` are not search syntax: punctuation is ignored and
  operator words must appear in the passage like any other word.
- `limit` is 1 to `MAX_PASSAGE_RESULTS`. There is no paging.
- `sections` keeps only passages from those sections, named as in
  `SECTION_NAMES` (case and surrounding spaces are ignored).
- Retracted or replaced articles drop out as soon as their text is removed.

`PassageMatch` has:

| Field | Meaning |
| --- | --- |
| `chunk` | `StoredChunk`: `pmcid`, `pmcid_version`, `position`, `section_index`, `section_name`, `text`, `token_count`, `licence` |
| `document` | The `FullTextDocument` the passage comes from |
| `score` | BM25 relevance, higher is better; compare only within one search |

A presentation layer must show `document.attribution` with any passage text
it displays, and should show `document.notice_flags` when not empty
(CONTENT_POLICY.md).

Errors: `InvalidRequestError` (no words in `query`, bad `limit`, unknown
section), `PaperStorageError`.

## Not provided yet

Deleting papers, paging inside a list or through passage results, progress
reporting during `ingest` or `fetch_fulltext`, other citation styles such as
APA, meaning-based (embedding) passage search, and PubMed abstract text.
