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
