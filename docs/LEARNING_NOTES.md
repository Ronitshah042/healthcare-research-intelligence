# Learning Notes

These notes explain the code currently in `src/ingestion`, starting with the
Milestone 1 metadata foundation in `metadata.py`. They are not a specification
for future features.

## Frozen dataclasses

A dataclass is a Python feature for representing structured data. Instead of
writing an `__init__` method by hand, the decorator creates one from annotated
fields.

```python
@dataclass(frozen=True)
class PaperMetadata:
    title: str
    authors: tuple[str, ...]
```

`frozen=True` prevents reassignment after construction. For example,
`paper.title = "New title"` raises an error. This suits validated metadata:
after normalisation, downstream code should be able to rely on its values not
being changed accidentally. The implementation does not use `slots=True`, so
it remains compatible with the Python 3.9 interpreter used during testing.

## Type hints

Type hints document the expected shape of data for readers and tools. They do
not, by themselves, validate runtime input.

```python
def normalise_paper_metadata(metadata: Mapping[str, object]) -> PaperMetadata:
```

This says the function expects keyed input and returns a `PaperMetadata`.
Runtime checks such as `isinstance(value, str)` are still needed because data
from a file or future API may not follow the type hint.

`str | None` means a field can contain either text or `None`. In this project,
an absent or blank abstract becomes `None`.

## Mappings and metadata input

A mapping is a collection of key/value pairs, such as a dictionary. The
normaliser reads fields with `.get()`:

```python
title = metadata.get("title")
```

If `title` is missing, `.get()` returns `None`; the required-text validator
then raises `MetadataValidationError`. This is why a missing required key and a
key explicitly set to `None` are both rejected.

Example input:

```python
raw_paper = {
    "title": "  Sleep and exercise  ",
    "authors": [" Ada Lovelace "],
    "publication_year": "2025",
    "source_identifier": " PMID: 12345 ",
}
```

## Text normalisation

The helper below removes outer whitespace and turns internal whitespace runs
into one normal space:

```python
" ".join(value.split())
```

For example, `"  A  short\nabstract.  "` becomes `"A short abstract."`.
The same rule applies to titles, author names, source identifiers, abstracts,
and source URLs. For identifiers, this deliberately does not validate a DOI or
PubMed format; it only cleans whitespace and rejects an empty result.

## Validation and exceptions

Validation checks whether input meets the module's rules before constructing a
trusted `PaperMetadata` object. When it does not, the module raises:

```python
class MetadataValidationError(ValueError):
    pass
```

Making it a subclass of `ValueError` communicates that the supplied value is
invalid while allowing callers to catch this project-specific error.

```python
try:
    normalise_paper_metadata({"title": "Only a title"})
except MetadataValidationError as error:
    print(error)
```

The actual function validates four required fields: title, authors,
publication year, and source identifier.

## Tuples for authors

The input may use a list because lists are convenient to build:

```python
"authors": [" Ada Lovelace ", "Grace\tHopper"]
```

After validation, the result stores authors as a tuple:

```python
("Ada Lovelace", "Grace Hopper")
```

Tuples cannot be altered in place. Together with the frozen dataclass, this
makes the validated author list stable. The code requires a `Sequence`, so
lists and tuples are suitable; a generator is not accepted. The sequence may be
empty, because some real citations (editorials, news items, older records)
list no authors; each entry that is present must still be non-empty text.

## Publication-year validation

Years may be an integer or a string of ASCII digits (`0`-`9`). The code excludes booleans
first because Python treats `bool` as a subtype of `int`.

```python
latest_valid_year = date.today().year + 1
```

The allowed range is `1` through that dynamic upper bound. For example, a
future online publication dated next calendar year is permitted, while a value
two years ahead is rejected. This rule changes automatically as the calendar
year changes.

### Resolved Unicode digit edge cases

Python's `.isdigit()` is true for many non-ASCII characters. Some, such as the
superscript `"²"`, cannot be converted by `int()`, which used to raise a plain
`ValueError`. Others, such as Arabic-Indic `"٢٠٢٤"` or full-width `"２０２４"`,
convert successfully and were silently accepted as the year 2024.

The code now checks strings with `re.fullmatch(r"[0-9]+", ...)`, so only ASCII
digits are accepted and every other digit-like string raises
`MetadataValidationError`. Regression tests cover all three examples.

## Basic URL validation

When a URL is provided, `urlsplit` checks its structure. The implementation
requires an `http` or `https` scheme and a non-empty network location (host).

```python
"https://example.org/papers/12345"  # accepted structure
"ftp://example.org/paper"           # rejected scheme
"/papers/12345"                     # rejected: no scheme or host
```

This does not contact the URL, confirm that it exists, verify a publisher, or
fully validate every part of a URL. It is intentionally a basic local check.

### Parser errors and the public validation contract

Some malformed URLs cannot be structurally split at all. For example, a URL
with an invalid bracketed host can make `urlsplit` raise `ValueError` before
the scheme-and-host checks run. That is still invalid user-supplied metadata,
so `_normalise_source_url` catches this parser error and raises
`MetadataValidationError` instead. This gives callers one documented exception
type for invalid metadata without hiding unexpected errors outside this narrow
parsing operation.

## `unittest`

`unittest` is built into Python. The project therefore needs no test package
to run its current tests. The metadata test class has 17 test methods. It
verifies:

1. a valid metadata dictionary is cleaned and converted correctly;
2. each required field is rejected when absent;
3. empty and whitespace-only required values are rejected;
4. invalid author containers and entries are rejected;
5. optional abstract and URL values become `None` when omitted, `None`, or
   blank, and reject invalid types;
6. basic valid and invalid URL structures behave as designed;
7. year boundaries, booleans, floats, and decimal-looking strings behave as
   designed;
8. non-ASCII digit strings raise `MetadataValidationError`, whether or not
   `int()` could convert them;
9. malformed URLs that make `urlsplit` fail still raise
   `MetadataValidationError`;
10. non-mapping metadata input is rejected with `MetadataValidationError`; and
11. validated `PaperMetadata` fields cannot be reassigned.

The successful local command was:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`PYTHONPATH=src` lets Python find the package in the `src` directory for this
minimal project setup. The Unicode case was initially an expected failure in
Milestone 1.1; it is now an ordinary passing regression test.

## Interview practice

### Why use a frozen dataclass for `PaperMetadata`?

It gives a concise model for validated data and prevents field reassignment
after the object is created. This reduces accidental mutation between ingestion
steps.

### Why are type hints not enough to validate metadata?

Hints describe intent to developers and static tools, but runtime input can
still be malformed. The code therefore uses `isinstance` checks and raises a
specific exception.

### Why convert authors from a list to a tuple?

Lists are useful input containers, but a tuple represents the final,
validated author sequence more safely because it cannot be changed in place.

### Why reject booleans as publication years?

In Python, `True` and `False` behave like integers (`1` and `0`). The explicit
boolean check prevents `True` from being mistaken for publication year 1.

### What does the URL check guarantee?

Only that a supplied value has basic HTTP(S) URL structure: an allowed scheme
and a host. It does not guarantee a live, trustworthy, or research-related
source.

### What happens to extra input fields?

They are ignored because the normaliser reads only known keys. This is a
current trade-off, not proof that unknown fields are always safe to ignore.

### How was the Unicode digit conversion issue handled?

The validator accepts only ASCII digits, checked with a regular expression
before `int()` runs. That rejects digit-like characters `int()` cannot convert
(`"²"`) and ones it can (`"٢٠٢٤"`), so callers always receive
`MetadataValidationError` and no unexpected year slips through.

## Milestone 2A: PubMed citation search

### Why use ESearch followed by EFetch?

ESearch answers two questions cheaply: how many PubMed records match and where
NCBI stored that result set on its temporary history server. EFetch can then
retrieve just the requested page from that set. This avoids fetching every
matching citation merely to show one page.

The client sends `usehistory=y` to ESearch, then supplies its `WebEnv` and
`QueryKey` to EFetch with `retstart` and `retmax`. The project caps `retmax` at
20 because the public result model promises no more than 20 records per page.

### Why have a title fallback?

A title-focused search uses PubMed's `[ti]` field. If it has no matches, the
client makes one broader `[tiab]` search over title/abstract-related content.
That keeps title searches precise when they work while still finding a citation
whose important terms appear outside its title. Author, DOI, and PMID lookups
do not fall back because changing their field would change the identity or
author intent of the request.

### Why is the returned year not always the same as a PubMed date filter?

The returned `PaperMetadata.publication_year` is the journal citation's
`PubDate/Year`. If that field is absent, the client takes the first four-digit
year in `MedlineDate`; it never substitutes an electronic `ArticleDate` or a
publication-history date. This makes the model's meaning predictable.

PubMed's `[pdat]` search filter, however, uses PubMed's publication-date
index. An online-first date and a later print citation can differ, so a record
found by a year filter can legitimately have a different displayed citation
year. The service reports this limitation instead of implying an exact match
across every date associated with a publication.

### Why keep abstracts out of the result model for now?

PubMed XML may contain publisher-supplied abstract text, but availability is
not a blanket reuse permission. Milestone 2A therefore always passes `None`
for `PaperMetadata.abstract`, does not cache API data, and does not fetch full
text. A later feature needs explicit scope approval and a rights review before
it can display or persist abstracts.

### Why inject the transport, clock, and sleeper?

An injected transport lets tests return prepared XML or failures without using
the network. Injected time functions verify the request limiter without making
tests wait. These small seams make the production client deterministic to test
while keeping its default implementation in the Python standard library.

### Why reject `NaN`, infinity, and custom endpoint hosts?

`NaN` is unusual because comparisons such as `NaN <= 0` are false. A simple
positive-number check can therefore accidentally accept it, after which a rate
limiter cannot calculate a meaningful interval. The configuration now requires
finite positive timeout and request-rate values.

Each E-utilities request includes a tool name and developer email, and an API
key when configured. The base URL is consequently restricted to HTTPS on
NCBI's official E-utilities host. Tests use an injected transport rather than
a custom endpoint, so this safety rule does not make tests depend on the
network.

### Why lock only the rate-limit scheduler?

The shared value that matters for rate limiting is the next reserved request
time. Two threads that read and update it independently can reserve the same
time slot. The client therefore holds a lock while it checks that value, waits,
and reserves a new slot. It releases the lock before the HTTP request, so one
slow response does not stop another already-scheduled request from starting.

### Why reject query syntax instead of trying to escape everything?

PubMed field tags, parentheses, quotes, and Boolean operators have useful
meaning in an advanced query, but these focused methods promise a particular
field. Correctly preserving every literal use of PubMed syntax would require a
larger query-language design. For this milestone, title, title/abstract, and
author inputs accept ordinary text with common punctuation and reject syntax
that can change the search. DOI and PMID lookups instead accept one validated
identifier. A future advanced-query feature can be designed separately.

### Why reject an empty EFetch document after a positive search?

ESearch and EFetch are linked through NCBI's history server. Once an in-range
page has a positive ESearch count, an XML document with no citation elements is
not a useful empty result: it could be an unexpected response shape or a stale
history result. Raising a response error lets the caller distinguish that case
from a genuine zero-result search. Individual `PubmedArticle` elements can
still be malformed; those remain visible through `invalid_record_count`.

## Search pipeline and local storage

### How does the pipeline remove duplicates?

A PubMed page can contain the same paper twice, sometimes with only a PMID on
one copy and only a DOI on the other. The pipeline groups records that share a
PMID or a DOI using a small "union-find" structure: each record starts in its
own group, and groups join when they share an identifier. Grouping is
transitive, so A-shares-PMID-with-B and B-shares-DOI-with-C makes one group.

Two groups never join if that would put two different PMIDs (or two different
DOIs) in one group. An article and its erratum can share a DOI but have
different PMIDs; merging them would silently drop one. Titles are never
compared, because similar titles do not prove two records are the same paper.

### Why SQLite for local storage?

SQLite is a full database stored in a single file and built into Python's
standard library, so it needs no server or extra package. `PaperStore` uses
`?` placeholders for every value, which keeps user text from being read as SQL.
`UNIQUE` columns stop two rows from claiming the same PMID or DOI, and
`BEGIN IMMEDIATE` makes "look for a match, then insert or update" one step that
another process cannot interrupt.

### Why decode authors before searching?

Authors are stored as a JSON list such as `["Ada Lovelace", "Anna M\u00fcller"]`.
JSON escapes non-ASCII letters, so searching that raw text for `müller` fails,
and punctuation such as `[` or `,` matches every row. Search therefore decodes
the list and matches the real names.

### How does Boolean search work?

The query is first split into tokens (terms, quoted phrases, `AND`, `OR`,
`NOT`, and parentheses), then parsed into a tree using precedence: `NOT` binds
tightest, then `AND`, then `OR`, so `a OR b AND c` means `a OR (b AND c)`. A
word only counts as an operator when a space, parenthesis, quote, or the end of
the query follows it, so `not-for-profit` stays an ordinary term. `NOT (a AND
b)` is evaluated with De Morgan's law as `NOT a OR NOT b`.

### What does fuzzy search add?

It accepts small spelling mistakes in titles. Each keyword is compared with
each title word using `difflib.SequenceMatcher`, a standard-library similarity
score from 0 to 1, and a match needs at least `0.85`. Each title word can match
only one keyword, so one similar word cannot satisfy a whole query. Exact
keyword matches always rank above fuzzy ones.

### Why do identifiers need one shared clean-up module?

The same paper can be written `10.1234/x`, `doi:10.1234/x`, or
`https://doi.org/10.1234/x`, and a PMID can be typed `123` or `0123`. If the
search client, the pipeline, and the database each decide separately which
spellings are equal, one layer will treat two spellings as one paper while
another treats them as two. `identifiers.py` holds those rules once, so every
layer gives the same answer. Each layer still does its own validation, because
they react differently to bad input: the client raises an error, the pipeline
drops the identifier, and the store refuses to save.

### Why count book records instead of returning or ignoring them?

PubMed also indexes books, and EFetch returns them in a different XML shape
(`PubmedBookArticle`) with no journal or journal date. Silently ignoring them
made a page look complete when it was not. Counting them in
`invalid_record_count` keeps the numbers honest: the caller can see that
PubMed matched something this project does not yet represent.

### How does attribution survive being stored?

Attribution is a display obligation: whenever a PubMed-derived citation is
shown, the PubMed source line and disclaimer must be shown with it. A live
search result can simply carry the attribution object. A database row cannot
hold a Python object, so the store saves a short label instead, the
`source_name` (`"PubMed"`), and `StoredPaper.attribution` turns that label back
into the attribution object when the paper is read.

Storing the label rather than assuming "everything here is from PubMed" keeps
the claim true for each paper individually, which matters once a second source
exists.

### What is a schema migration?

A database table has a fixed list of columns, its "schema". Adding
`source_name` means databases created earlier lack that column. A migration is
the small upgrade step that fixes this: on opening, `PaperStore` asks SQLite
which columns exist (`PRAGMA table_info`), and if `source_name` is missing it
runs `ALTER TABLE ... ADD COLUMN` and fills in a value for the existing rows.
The check makes the step safe to run every time: a database that is already
up to date is left alone.

### Why is `attribution` a property rather than a stored field?

A property is computed each time it is read. Because `attribution` is worked
out from `source_name`, the two can never disagree, which they could if both
were stored separately.

## Phase 1: command line and service layer

### What is a service layer, and why not put the logic in the CLI?

A program usually has more than one "front door": a command line today, a web
page or API tomorrow. If the logic for "search papers" lives inside the code
that prints search results, the next front door has to copy it, and the copies
drift apart.

A service layer is the set of functions behind all the doors. Each one takes
ordinary arguments and returns an object describing the result:

```python
results = services.search_papers("sleep", page=2)
results.total_count      # a number
results.papers[0].title  # text
```

The command line then has only three jobs: read what the user typed, call one
service function, and print the result. Returning objects instead of printed
text is what makes the function reusable, because a web page wants a table, a
test wants to compare numbers, and neither wants to pick apart a printed
string.

### What does `argparse` do?

`argparse` is the standard-library tool for reading command-line arguments. You
declare what the program accepts, for example an option `--max` that must be a
whole number, and it turns `["--max", "200"]` into `args.max_records == 200`,
prints help for `--help`, and stops with an error and exit code 2 when the
input does not fit. "Subcommands" such as `hri ingest` and `hri search` are
separate small parsers attached to the main one.

### What is an exit code?

Every command-line program returns a number when it ends. `0` means success;
anything else means failure. Scripts use it to decide whether to continue.
`hri` returns `2` when the command was written wrongly (bad option, malformed
query) and `1` when the command was fine but could not be completed (paper not
found, PubMed unreachable).

### What is a console script?

`pyproject.toml` contains the line `hri = "hri.cli:main"`. When the project is
installed with `pip install -e .`, pip creates a small `hri` program that
imports `hri.cli` and calls `main()`. The `-e` ("editable") flag makes the
installed command use the source files in place, so code changes take effect
without reinstalling.

### What does paging need from the database?

To show "page 2 of 7", a UI needs two things: the 20 rows for this page and
the total number of matches. `search_with_total` returns both. The page is
`matches[offset : offset + limit]` with `offset = (page - 1) * page_size`, and
the number of pages is the total divided by the page size, rounded up.

### Why can a spreadsheet file be dangerous?

If a CSV cell begins with `=`, spreadsheet programs treat it as a formula and
run it. A paper title is outside our control, so a title such as
`=HYPERLINK(...)` could do something the user did not intend when they open an
export. The export puts a `'` in front of such cells, which tells the
spreadsheet "this is text".

### How do tests exercise the CLI without a network or a terminal?

`main()` accepts the argument list as a parameter, so a test calls
`cli.main(["search", "sleep"])` directly. Output is captured by temporarily
replacing standard output with an in-memory text buffer
(`contextlib.redirect_stdout`). For `ingest`, the test replaces the function
that builds the real PubMed client with one returning a fake client
(`unittest.mock.patch`), so the whole path from command line to database runs
without contacting PubMed.

## Before Phase 2: citations, list management, and concurrency

### What is Vancouver (NLM) style?

It is the citation layout used by most medical journals and by PubMed itself:

```text
Polack FP, Thomas SJ, Kitchin N, et al. Safety and Efficacy of the BNT162b2
mRNA Covid-19 Vaccine. N Engl J Med. 2020;383(27):2603-2615.
doi: 10.1056/nejmoa2034577. PMID: 33301246.
```

Reading left to right: authors as surname plus initials (the first six, then
"et al"), the title, the journal's official abbreviation, then
`year;volume(issue):pages`, and finally identifiers.

### Why could the names not just be rearranged?

A stored name is one piece of text, `Fernando P Polack`. Turning it into
`Polack FP` means guessing where the surname starts. The guess fails for
`Johannes van der Waals` (surname `van der Waals`) and for group authors such
as `WHO Study Group`, which is not a person at all. PubMed already supplies
the surname and initials separately, so the reliable approach is to keep that
form alongside the display name rather than reconstruct it later.

### Why did tests miss the doubled initials?

Tests can only catch what their example data contains. The fixtures had
authors with a forename *or* initials, never both, while real PubMed records
always have both. The lesson is to build at least one fixture from the shape
real data takes, which the new regression test now does.

### What are WAL mode and a busy timeout?

SQLite allows one writer at a time. In its default mode a writer also blocks
readers. In write-ahead logging (WAL) mode, changes go first to a separate
`-wal` file, so readers keep working from the main file while a write is in
progress.

A second *writer* still has to wait its turn. The busy timeout says how long
it waits before giving up: without one, it fails immediately with "database is
locked"; with a 10-second timeout, it simply pauses until the first writer
finishes.

## Phase 2: open-access full text

### What do the Creative Commons licences allow?

Authors and publishers choose a licence that says what others may do with an
article. The ones that matter here:

| Licence | Meaning |
| --- | --- |
| CC0 | Public domain: no conditions at all. |
| CC BY | Reuse freely, including commercially, but credit the source and say what you changed. |
| CC BY-SA | As CC BY, but anything built from it must use the same licence. |
| CC BY-NC | Non-commercial use only. |
| CC BY-ND | No derivatives: the text must not be changed. Splitting it into passages arguably changes it. |

The project stores only CC0 and CC BY by default because they are the only
ones that clearly allow storing, splitting, and quoting text in any later use.
"Open access" alone is not enough: an article can be free to read but still
forbid reuse.

### What is JATS XML?

JATS (Journal Article Tag Suite) is the standard XML format for journal
articles. Every part of an article is marked up: `<article-title>`,
`<abstract>`, `<sec>` for a section with its `<title>`, `<p>` for a
paragraph, `<xref ref-type="bibr">` for a citation marker, `<table-wrap>`
and `<fig>` for tables and figures, and `<ref-list>` for the references.
Parsing means walking this tree, keeping the prose, and dropping the rest.

### Why read the licence before downloading the article?

The PMC Cloud Service keeps a small metadata file for each article version,
with the licence as a plain field (`"license_code": "CC BY"`). Reading it
first means text under a licence we cannot use is never downloaded at all,
which is cleaner than downloading the article, finding its licence inside, and
throwing it away.

### What is a host allowlist?

The client builds every URL itself and refuses any URL whose host is not on a
fixed list of three. A mistake elsewhere in the code, or an unexpected URL in
a server response, therefore cannot make it contact another server or send
the email address or API key somewhere else.

### Why split articles into chunks?

Later phases will search passages and quote them as evidence. A whole article
is too long to compare with a question, and a single sentence lacks context.
Passages of a few hundred words are a common middle ground. The overlap
repeats the last sentences of one chunk at the start of the next, so an idea
that spans a boundary is still found whole in at least one chunk. Chunks never
cross a section boundary, so a passage from Methods is never mixed with one
from Results.

### Why keep a record of rejections?

A rejected article stores no text, but recording its PMCID, licence, and reason
makes the policy auditable: anyone can see what was turned away and why, and a
count of rejections by reason shows whether the policy is working as intended.

### Why re-check articles after storing them?

Science corrects itself: an article can be retracted months after
publication. Stored text from a retracted article would otherwise keep being
used as evidence. `recheck_fulltext` asks PMC again for each stored article's
status and deletes any that are now retracted.

## Phase 3: keyword search over passages

### What is a full-text index?

Searching every passage for a word one by one gets slow as the collection
grows. A full-text index works like the index at the back of a book: for every
word it lists the passages that contain it, so a search looks the words up
instead of reading everything. SQLite has one built in, called FTS5.

### What is BM25?

BM25 is the standard way to rank keyword matches. A passage scores higher
when the query words appear in it often, when those words are rare across all
passages (a rare word says more than a common one), and when the passage is
short (a word in a short passage counts for more). SQLite's `bm25()` returns
negative numbers where lower is better; the project flips the sign so a
higher `score` means more relevant. Scores compare passages within one search
only.

### What is stemming?

Stemming cuts words down to a common root, so `veteran` and `veterans`, or
`treat` and `treated`, match each other. The Porter stemmer does this for
English with simple rules. It is not perfect (it does not know that "mice" is
the plural of "mouse"), but it catches most word-ending differences.

### Why quote every word?

FTS5 has its own query language: `OR`, `NOT`, `NEAR(...)`, `*` for prefixes,
and `column:` filters. Passing user text straight in could cause syntax
errors or change what is searched. Putting each word in double quotes makes
FTS5 treat it as a plain word, much as a parameterised SQL query keeps input
from becoming SQL.

### Keyword search versus meaning-based search

Keyword search finds passages that use the same words as the question.
Meaning-based (embedding) search turns text into lists of numbers so that
passages with similar meaning sit close together, even when they use different
words. Hybrid search combines both. This phase builds only the keyword half.

## Phase 3B: meaning-based (embedding) search

### What is an embedding?

An embedding model turns a piece of text into a list of numbers (here 384 of
them), called a vector. Texts with similar meaning get vectors that point in
similar directions, even when they share no words: "heart attack" and
"myocardial infarction" end up close together. Comparing two vectors is a
simple sum of products (cosine similarity); 1 means "same direction".

### Why are questions and passages embedded differently?

BGE models were trained to match a short question with a longer passage that
answers it. They work best when the question starts with an instruction,
"Represent this sentence for searching relevant passages: ", and the passage
does not. fastembed's `query_embed` and `passage_embed` keep the two apart,
but for this model fastembed adds no instruction itself, so the project adds
it to questions.

### What is reciprocal rank fusion?

Keyword scores (BM25) and similarity scores are on different scales, so they
cannot simply be added. Reciprocal rank fusion ignores the scores and uses
positions instead: a passage gets `1 / (60 + rank)` from each list it appears
in. Being near the top of both lists beats being first in only one. It needs
no tuning and is the usual way to build hybrid search.

### Why does the 512-token limit matter?

The model reads at most 512 tokens (word pieces, so medical terms often count
as several). Anything after that is cut off before embedding, so the vector
describes only the start of a long passage. `hri embed-passages` counts how
often this happens, using the model's own tokenizer, so the passage size can
be tuned if it is common. Keyword search always sees the whole passage.

### Pinning a model

A model name alone can point to different files over time. Pinning the
repository revision (a Git commit hash) and checking the downloaded file's
SHA-256 makes every install use exactly the same model, and refuses a file
that was altered or corrupted.

## Phase 4: answers from evidence

### What is RAG?

Retrieval-augmented generation: instead of letting a language model answer
from memory, the program first retrieves relevant passages, then gives them
to the model with the question and asks it to answer only from them. The
answer can then be checked against the passages it cites.

### Why check citations after generation?

Language models sometimes invent references. Because every passage sent to
the model has a number, any `[n]` that names a passage that was not sent is
provably made up and is removed. If too many are made up, or none are real,
the answer is withheld rather than shown.

### What is prompt injection?

Text that tries to give the model new instructions, hidden in data the model
reads, such as "ignore all rules" inside a passage. The defence here is
layered: passages sit inside delimiters they cannot close, the rules say
evidence text is never instructions, and the citation check catches invented
references even if the model is fooled.

### Why abstain?

A research aid that says "the stored evidence is not sufficient" is more
useful than one that guesses. The pipeline abstains before calling the model
when too little or only off-topic evidence was found, and after, when the
model says so or the citations do not hold up.

### Free tiers

Free AI tiers limit requests per minute and per day, and may use what is sent
to improve the provider's products. The cache avoids paying twice for the
same question and evidence, the daily cap keeps usage predictable, and a
second provider takes over when the first refuses.
