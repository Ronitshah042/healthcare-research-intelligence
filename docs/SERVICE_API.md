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
- **No function contacts the network except `ingest`.**
- **`database_path`** is an optional keyword on every function. When omitted,
  the path is `HRI_DB_PATH` if set, otherwise `data/hri.sqlite3` relative to
  the current working directory. The database and its folder are created on
  first use and upgraded automatically if they come from an older version.
- **Calls are independent.** Each one opens and closes its own database
  connection, so functions can be called from different threads or requests.
- **Attribution must be displayed.** Wherever a result has an `attribution`
  that is not `None`, a UI must show its four text fields alongside the data
  (see [Attribution](#attribution)).
- **Abstract text is never available.** Only the `abstract_available` flag is
  stored; link to PubMed for the abstract itself.

## Quick reference

| Function | Purpose | Returns |
| --- | --- | --- |
| [`ingest`](#ingest) | Fetch PubMed citations and save them | `IngestResult` |
| [`search_papers`](#search_papers) | Search stored papers | `SearchResults` |
| [`get_paper`](#get_paper) | One stored paper by PMID or DOI | `StoredPaper` |
| [`get_stats`](#get_stats) | Counts of stored papers | `PaperStoreStats` |
| [`create_list`](#create_list) | Create an empty saved list | `SavedList` |
| [`add_to_list`](#add_to_list) | Add a stored paper to a list | `ListChange` |
| [`remove_from_list`](#remove_from_list) | Remove a paper from a list | `ListChange` |
| [`get_lists`](#get_lists) | All saved lists | `tuple[SavedList, ...]` |
| [`get_list`](#get_list) | One list with its papers | `ListContents` |
| [`export_list`](#export_list) | A list as CSV or citation text | `ListExport` |

## Errors

All are importable from `hri.services`.

| Error | Meaning | Raised by |
| --- | --- | --- |
| `InvalidRequestError` | An argument is unusable: blank query, unknown mode or strategy, bad page or year, malformed Boolean query, malformed PMID/DOI, bad list name, unknown export format. Subclass of `ValueError`. The message is safe to show to a user. | every function that takes arguments |
| `PaperNotFoundError` | No stored paper has that PMID or DOI. | `get_paper`, `add_to_list` |
| `SavedListNotFoundError` | No saved list has that name. | `add_to_list`, `remove_from_list`, `get_list`, `export_list` |
| `SavedListExistsError` | A list with that name already exists (names ignore case). | `create_list` |
| `PaperStorageError` | The database could not be read or written, or was created by a newer version of this project. | every function |
| `PubMedConfigurationError` | `PUBMED_NCBI_TOOL` or `PUBMED_NCBI_EMAIL` is missing or invalid. Subclass of `ValueError`, but **not** of `InvalidRequestError`. | `ingest` |
| `PubMedError` | PubMed could not be reached or returned something unusable. Subclasses: `PubMedTimeoutError`, `PubMedTransportError`, `PubMedHttpError` (has `.status_code`), `PubMedResponseError`. Messages never contain the API key, request URL, or NCBI error text. | `ingest` |

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
| `attribution` (property) | `PubMedAttribution \| None` | Set when `source_name` is `"PubMed"`. |
| `pubmed_url` (property) | `str \| None` | `https://pubmed.ncbi.nlm.nih.gov/<pmid>/` for PubMed papers with a PMID. Use this for the "view on PubMed" link. |

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
| `EXPORT_FORMATS` | `("csv", "citations")` |
| `DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE` | `20`, `100` |
| `DEFAULT_INGEST_RECORDS`, `MAX_INGEST_RECORDS` | `100`, `1000` |

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
| `export_format` | `str` | `"csv"` or `"citations"` (`services.EXPORT_FORMATS`). |
| `content` | `str` | The document, UTF-8 text with `\n` line endings. |
| `paper_count` | `int` | |
| `attribution` | `PubMedAttribution \| None` | |

- **`csv`:** a header row, then one row per paper. Columns: `pmid`, `doi`,
  `title`, `authors` (joined with `; `), `journal`, `publication_year`,
  `abstract_available` (`yes`/`no`), `source`, `url`. A cell that starts with
  `=`, `+`, `-`, or `@` is prefixed with `'` so spreadsheet programs do not run
  it as a formula. The attribution is **not** inside the CSV, so display
  `ListExport.attribution` wherever the file is offered. An empty list gives
  the header row only.
- **`citations`:** one numbered line per paper in the form
  `Authors. Title. Journal. Year. PMID: n. doi:x. link`, omitting missing
  parts, followed by a blank line and the attribution text. This is a readable
  layout, not a formal style such as Vancouver or APA. An empty list gives an
  empty string.

Errors: `InvalidRequestError` (unknown format or bad name),
`SavedListNotFoundError`, `PaperStorageError`.

## Not provided yet

Deleting papers or lists, renaming lists, paging inside a list, progress
reporting during `ingest`, and abstract text.
