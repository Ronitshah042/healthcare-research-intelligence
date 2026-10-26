# Learning Notes: Milestone 1 Metadata Foundation

These notes describe the code currently in `src/ingestion/metadata.py`. They
are not a specification for future features.

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
lists and tuples are suitable; a generator is not accepted.

## Publication-year validation

Years may be an integer or a digit-only string. The code excludes booleans
first because Python treats `bool` as a subtype of `int`.

```python
latest_valid_year = date.today().year + 1
```

The allowed range is `1` through that dynamic upper bound. For example, a
future online publication dated next calendar year is permitted, while a value
two years ahead is rejected. This rule changes automatically as the calendar
year changes.

### Resolved `.isdigit()` and `int()` edge case

The code checks a string
with `.isdigit()` and then converts it using `int()`. Certain Unicode digit-like
characters may satisfy `.isdigit()` yet not be parseable by `int()`. If that
happens, Python may raise a normal `ValueError` instead of
`MetadataValidationError`.

The approved follow-up catches `ValueError` from the conversion and raises
`MetadataValidationError` instead. The superscript digit `"²"` is now covered
by an ordinary passing regression test. The rule is otherwise unchanged:
numeric strings must still satisfy `.isdigit()` before a conversion is
attempted.

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
to run its current tests. After Milestone 1 maintenance, the test class has 16
discovered test methods. It verifies:

1. a valid metadata dictionary is cleaned and converted correctly;
2. each required field is rejected when absent; and
3. empty and whitespace-only required values are rejected;
4. invalid author containers and entries are rejected;
5. optional abstract and URL values become `None` when omitted, `None`, or
   blank, and reject invalid types;
6. basic valid and invalid URL structures behave as designed;
7. year boundaries, booleans, floats, and decimal-looking strings behave as
   designed; and
8. Unicode digits that cannot be converted to an integer raise
   `MetadataValidationError`.
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
Milestone 1.1; after the approved follow-up, the same 13-test suite has no
expected failures.

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

The validator catches `ValueError` from `int()` and converts it into
`MetadataValidationError`. The `"²"` regression test verifies callers receive
the project's consistent validation exception.

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
