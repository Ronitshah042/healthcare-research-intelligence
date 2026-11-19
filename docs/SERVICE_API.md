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
| [`search_passages`](#search_passages) | Hybrid (or keyword-only) search of stored passages | `PassageSearchResult` |
| [`prepare_embedder`](#prepare_embedder) | Download (once) and load the embedding model | `EmbedderStatus` |
| [`embed_passages`](#embed_passages) | Embed stored passages that have no vector yet | `EmbedPassagesResult` |
| [`run_eval`](#run_eval) | Measure retrieval and answer quality on a question set | `EvalResult` |
| [`ask_question`](#ask_question) | Answer a research question from stored passages, with checked citations | `Answer` |

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
| `EmbedderUnavailableError` | Meaning-based search cannot run: the optional `fastembed` install is missing, or the model could not be downloaded, failed its SHA-256 check, or could not be loaded. The message says which, and never contains server text. | `embed_passages` |

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
| `SEARCH_METHODS` | `("hybrid", "keyword")` |
| `DEFAULT_EVIDENCE`, `MAX_EVIDENCE` | `6`, `10` (passages given to the AI model) |
| `MAX_QUESTION_LENGTH` | `1000` characters |
| `DEFAULT_PACE_SECONDS`, `DEFAULT_REPORT` | `5.0`, `docs/EVAL_RESULTS.md` (for `run_eval`) |
| `DISCLAIMER` | The fixed research-aid disclaimer carried by every `Answer` |
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
status are read **before** any text is downloaded, together with PubMed's
correction links (CONTENT_POLICY.md section 6). Retracted articles, including
those PubMed links to a retraction,
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
| `flagged` | `int` | Stored articles carrying correction or concern flags, from the XML or PubMed. |
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
version if that has changed, and PubMed's correction links for articles with
a PMID. It **deletes any article that is now retracted** (by PMC or PubMed),
recording the rejection, and adds new correction or concern flags to the
articles it keeps. **Run it before every rebuild of a data
bundle.**

`FullTextRecheckResult` has `checked` (`int`), and four tuples of PMCIDs:
`removed_retracted`, `newer_version_available` (not fetched automatically;
run `fetch_fulltext` again), and `unavailable` (the stored version has left
the Cloud Service; its text is kept, because the licence it was stored under
still applies), and `newly_flagged` (articles whose `notice_flags` gained a
flag in this run).

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
    query, *, limit=10, sections=(), database_path=None, embedder=None
) -> PassageSearchResult
```

Searches the stored open-access passages, best match first. **It never
downloads anything.** When the embedding model has been prepared (see
`prepare_embedder`), the search is **hybrid**; otherwise it is keyword-only
and `notice` says why.

- **Keyword part:** a passage matches when it contains every word of `query`,
  ignoring case and accents, after English (Porter) stemming, ranked by BM25.
  `query` is always plain words: quotes, `*`, `:`, brackets and words such as
  `OR`, `NOT` or `NEAR` are not search syntax.
- **Meaning part:** the question is embedded with the BGE query instruction
  and compared with every embedded passage by cosine similarity.
- **Fusion:** the top 50 of each ranking are combined by reciprocal rank
  fusion, `sum(1 / (60 + rank))`, so passages found both ways come first.
- `limit` is 1 to `MAX_PASSAGE_RESULTS`. There is no paging.
- `sections` keeps only passages from those sections, named as in
  `SECTION_NAMES` (case and surrounding spaces are ignored).
- Retracted or replaced articles drop out of both indexes as soon as their
  text is removed.
- `embedder`: leave as `None`; tests pass a fake.

`PassageSearchResult`:

| Field | Meaning |
| --- | --- |
| `matches` | `tuple[PassageMatch, ...]` |
| `method` | `"hybrid"` or `"keyword"` (`SEARCH_METHODS`) |
| `notice` | `None`, or a sentence to show: why the search was keyword-only, or how many passages are not embedded yet |

`PassageMatch` has:

| Field | Meaning |
| --- | --- |
| `chunk` | `StoredChunk`: `pmcid`, `pmcid_version`, `position`, `section_index`, `section_name`, `text`, `token_count`, `licence` |
| `document` | The `FullTextDocument` the passage comes from |
| `score` | Higher is better; compare only within one search. BM25 for keyword-only searches, the fused score for hybrid ones |

A presentation layer must show `document.attribution` with any passage text
it displays, and should show `document.notice_flags` when not empty
(CONTENT_POLICY.md).

Errors: `InvalidRequestError` (no words in `query`, bad `limit`, unknown
section), `PaperStorageError`. An unavailable model is not an error here.

### `prepare_embedder`

```python
prepare_embedder(*, download=True) -> EmbedderStatus
```

**Call it once when an application starts.** It downloads the pinned model
from Hugging Face if it is not cached yet, checks its size and SHA-256, and
loads it, so later searches in the same process are hybrid. With
`download=False` it only loads an already-downloaded model. It never raises
for an unavailable model.

`EmbedderStatus` has `available` (`bool`), `model_id` (for example
`BAAI/bge-small-en-v1.5@aa8f8b0`), and `message` (`None` when available,
otherwise a sentence to show, such as the install command).

The model is cached in `HRI_MODEL_CACHE`, else `data/models`.

### `embed_passages`

```python
embed_passages(*, database_path=None, embedder=None) -> EmbedPassagesResult
```

Embeds every stored passage that has no vector from the current model yet,
downloading the model first if needed. Run it after `fetch_fulltext`; until
then, new passages are found by keyword only. It logs how many passages were
longer than the model's 512-token limit (only their first 512 tokens are
used).

`EmbedPassagesResult` has `model_id`, `embedded` (passages embedded in this
run), `over_token_limit` (of those, how many exceeded 512 model tokens), and
`longest_tokens`.

Errors: `EmbedderUnavailableError`, `PaperStorageError`.

### `ask_question`

```python
ask_question(
    question, *, evidence_count=6, database_path=None, llm=None, embedder=None
) -> Answer
```

Answers a research question using **only** stored open-access passages, and
checks every citation. Steps:

1. Retrieve passages: keyword search for any of the question's words (BM25),
   fused with meaning-based search when the embedding model is ready. Only
   CC0 and CC BY passages are kept, whatever `HRI_ALLOWED_LICENCES` allows,
   and article titles are never used as evidence.
   The best `evidence_count` are numbered 1 to n.
2. Abstain without calling any AI provider when fewer than 2 passages are
   found, or (hybrid search) the closest passage's similarity is below 0.7.
3. Otherwise send the question and the numbered passages to the AI provider
   (Gemini, then Groq if Gemini fails), unless the same question with the
   same passages was answered before (cache). Each passage is wrapped in
   `<evidence>` delimiters it cannot close, and the model is told to ignore
   any instructions inside it.
4. Check the reply: citations `[n]` to a passage that was not supplied are
   removed and counted. The answer abstains if no valid citation remains, if
   invented citations outnumber valid ones, or if the model reports the
   evidence insufficient.

It **never raises because of an AI provider**: rate limits, outages, missing
keys, and the daily request cap all give an abstained answer with
`abstain_reason="busy"` and a `notice` explaining why.

| Input | Rules |
| --- | --- |
| `question` | Non-empty text, at most 1000 characters. It is sent to the AI provider, so it must not contain personal data. |
| `evidence_count` | 2 to 10. |
| `llm`, `embedder` | Leave as `None`. Tests pass fakes. |

`Answer`:

| Field | Type | Meaning |
| --- | --- | --- |
| `question` | `str` | As asked, trimmed. |
| `text` | `str` | The answer with `[n]` citations, or the reason no answer was given. |
| `abstained` | `bool` | `True` when no evidence-based answer was given. |
| `abstain_reason` | `str \| None` | `insufficient_evidence`, `low_relevance`, `no_valid_citations`, `too_many_invalid_citations`, `model_insufficient`, or `busy`. |
| `citations` | `tuple[Citation, ...]` | Each valid citation once, in order of first use. |
| `evidence` | `tuple[PassageMatch, ...]` | The passages given to the model; `evidence[n - 1]` is `[n]`. |
| `invalid_citations` | `int` | Citations removed because they named no supplied passage. |
| `provider`, `model` | `str \| None` | Which AI produced the text, if any. |
| `cached` | `bool` | The model reply came from the cache. |
| `search_method` | `str` | `"hybrid"` or `"keyword"`. |
| `notice` | `str \| None` | Who received the question and passages (and, for Gemini's free tier, that Google may use them to improve its products), or why AI was not used. **Show it.** |
| `disclaimer` | `str` | Always `DISCLAIMER`. **Show it with every answer.** |

`Citation` has `number`, `pmcid`, `pmcid_version`, `pmid`, `doi`, `title`,
`section`, `link` (the PMC article page), `licence`, and `attribution`. Show
the attribution with any quoted or summarised text.

Configuration (environment variables; see `.env.example`): `GEMINI_API_KEY`,
`GROQ_API_KEY`, `HRI_GEMINI_MODEL` (default `gemini-3.5-flash-lite`),
`HRI_GROQ_MODEL` (default `qwen/qwen3.8-27b`), and
`HRI_LLM_DAILY_REQUEST_CAP` (default 100 requests per UTC day, counting every
provider attempt).

Errors: `InvalidRequestError` (bad question or `evidence_count`, or a
`HRI_LLM_DAILY_REQUEST_CAP` that is not a whole number), `PaperStorageError`.

### `run_eval`

```python
run_eval(
    question_set, *, database_path=None, max_questions=None, answers=True,
    report_path="docs/EVAL_RESULTS.md", spotcheck_dir=None, pace_seconds=5.0,
    judge_prompt_path="eval/judge_prompt.txt", label="run", summary_path=None,
    baseline_path=None,
) -> EvalResult
```

Runs a question set (JSON, see `eval/questions.json`) against the stored
passages and writes two files: the Markdown report at `report_path`, and
`spotcheck-<date>.csv` (next to the set unless `spotcheck_dir` is given),
a sheet of up to 15 judged claims with empty `your_verdict` and `your_notes`
columns for a human check.

- **Retrieval**, per article: recall@1/3/5, precision@1/3/5 and mean
  reciprocal rank for keyword (any-word BM25), semantic, and hybrid search,
  over the questions that have relevant articles. Semantic and hybrid need
  the embedding model; without it only keyword is scored.
- **Answers** (unless `answers=False`): each question goes through
  `ask_question`'s pipeline, then an AI judge on Groq (`GROQ_API_KEY`;
  `HRI_JUDGE_MODEL`, default `openai/gpt-oss-120b`; prompt in
  `eval/judge_prompt.txt`) rates every cited claim supported, partial, or
  unsupported. **A model never judges its own answers**: answers written by
  the judge model (for example via the Groq fallback) are left unjudged and
  counted. Without a Groq key the judge is not run. Reported: evidence recall
  (share of a question's relevant articles among the passages sent to the
  model), citation validity, verdict shares, correct abstention, answers
  given when expected, latency of uncached answers, the models that actually
  wrote the answers, and AI requests used.
- **Status:** while any question is marked `needs_review`, the report title
  says "preliminary (questions under review)".
- **Comparisons:** `summary_path` saves the headline numbers as JSON under
  `label`; `baseline_path` adds a before/after table against such a file.
- **Free tier:** answers and judgements are cached and reused, so a rerun
  costs nothing for unchanged questions; it waits `pace_seconds` after each
  question that used an AI request; `max_questions` takes the first N; the
  daily cap (`HRI_LLM_DAILY_REQUEST_CAP`) applies, and questions past it are
  reported as busy.

`EvalResult` has `report_path`, `spotcheck_path` (`None` without answers),
`questions`, `llm_calls`, `retrieval` (per method: `questions`,
`recall@k`, `precision@k`, `MRR`), and `answers` (the summary numbers).

Errors: `InvalidRequestError` (missing or invalid question set or baseline,
bad `max_questions` or `pace_seconds`), `PaperStorageError`, `OSError`
(report cannot be written).

## Not provided yet

Deleting papers, paging inside a list or through passage results, progress
reporting during `ingest` or `fetch_fulltext`, other citation styles such as
APA, embedding the question and passages with any other model, and PubMed
abstract text.
