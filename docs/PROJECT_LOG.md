# Project Log

## Initial repository setup

The repository began with project guidance in `AGENTS.md`, a high-level
project description in `README.md`, and a `.gitignore` for local secrets,
Python environments, caches, IDE files, and logs. No dependencies, API keys,
external services, downloaded papers, commits, or remote changes were added.

The first approved implementation milestone established a small Python
foundation for research-paper metadata ingestion. It deliberately does not
fetch papers, call external APIs, retrieve evidence, generate answers, or make
clinical claims.

## Milestone 1: metadata normalisation and validation

**Status:** Complete

### Files created

| File | Responsibility |
| --- | --- |
| `src/ingestion/__init__.py` | Makes the ingestion package importable and exposes the public metadata API. |
| `src/ingestion/metadata.py` | Defines the metadata model, input normalisation, and validation rules. |
| `tests/test_metadata.py` | Uses the standard-library `unittest` framework to test valid metadata, missing required fields, and invalid years. |

### Confirmed implementation behaviour

`normalise_paper_metadata` accepts a mapping and returns a frozen
`PaperMetadata` object. The required fields are `title`, `authors`,
`publication_year`, and `source_identifier`. `abstract` and `source_url` are
optional.

- Required text values must be strings and must not be empty after whitespace
  normalisation.
- Text normalisation collapses leading, trailing, repeated, tab, and newline
  whitespace to single spaces.
- Authors must be a non-empty sequence of non-empty strings. They are stored as
  a tuple.
- Publication years accept integers and digit-only strings. Booleans are
  explicitly rejected. Valid years are from `1` through the current calendar
  year plus one.
- Source identifiers are whitespace-normalised and must not be empty. The code
  deliberately does not require a PubMed, DOI, or other provider-specific
  format.
- An absent, `None`, or whitespace-only abstract becomes `None`.
- A source URL is optional. If supplied, it must have an `http` or `https`
  scheme and a host according to `urllib.parse.urlsplit`.
- Additional, unknown mapping keys are currently ignored.

Invalid inputs raise `MetadataValidationError`, except for the potential edge
case noted below.

### Test record

The requested command was attempted exactly as follows:

```text
PYTHONPATH=src python -m unittest discover -s tests -v
```

It could not start because this environment had no `python` executable:

```text
zsh:1: command not found: python
```

The equivalent command using the available interpreter succeeded:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Exact successful result:

```text
test_normalises_valid_metadata (test_metadata.NormalisePaperMetadataTests) ... ok
test_rejects_invalid_publication_years (test_metadata.NormalisePaperMetadataTests) ... ok
test_rejects_missing_required_fields (test_metadata.NormalisePaperMetadataTests) ... ok

----------------------------------------------------------------------
Ran 3 tests in 0.002s

OK
```

During verification, the available Python 3.9 interpreter also showed that
`dataclass(slots=True)` was unsupported. The final implementation therefore
uses `@dataclass(frozen=True)` without `slots=True`, retaining immutability
while supporting that interpreter.

### Remaining limitations

Milestone 1.1 adds coverage for whitespace-only values, invalid author input,
optional fields, basic URL structures, year boundaries and types, and the
Unicode-digit issue. The following behaviour remains untested:

- mappings other than ordinary dictionaries;
- the current behaviour for unknown metadata keys; and
- author generators, which are rejected because the implementation requires a
  `Sequence` rather than any iterable.

#### Potential bug to investigate: Unicode digit conversion

At the end of Milestone 1.1, this issue was **not fixed**. The year validation
first checked
`value.strip().isdigit()` and then calls `int(...)`. Some Unicode characters,
such as a superscript two (`"²"`), can report `True` for `.isdigit()` but may
not be accepted by `int()`. In that situation, the code may expose a built-in
`ValueError` instead of the project-specific `MetadataValidationError`.

## Milestone 1.1: metadata validation test coverage

**Status:** Complete

### Scope completed

`tests/test_metadata.py` was expanded from 3 to 13 discovered `unittest` test
methods without changing the metadata validator. The new coverage confirms:

- rejection of empty or whitespace-only titles, source identifiers, author
  entries, and empty author sequences;
- rejection of a string used as the whole author value, `None` authors, and
  sequences containing non-text entries;
- optional abstract and source URL behaviour when fields are omitted, `None`,
  blank, or have invalid types;
- acceptance of structurally valid HTTP and HTTPS source URLs and rejection of
  relative, scheme-less, FTP, and host-less URLs;
- acceptance of publication-year boundaries (`1` and current year plus one);
  and rejection of booleans, floats, and decimal-looking strings; and
- the desired project-specific exception contract for the Unicode-digit case.

### Unicode-digit result

The Unicode-digit test uses `@unittest.expectedFailure`. It expects
`MetadataValidationError` for the superscript digit `"²"`, but the current
implementation reaches `int("²")`, which raises a built-in `ValueError`
instead. The expected failure makes this unfixed discrepancy visible while
allowing the test suite to pass. No validator code was changed.

### Verification result

The required command was run:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Its exact summary was:

```text
Ran 13 tests in 0.009s

OK (expected failures=1)
```

The 12 non-expected-failure tests passed. The only expected failure was
`test_rejects_unicode_digit_year_with_metadata_validation_error`.

## Unicode publication-year validation fix

**Status:** Complete as an approved follow-up to Milestone 1.1

The publication-year validator now catches `ValueError` from `int(...)` when a
digit-only string cannot be converted, then raises `MetadataValidationError`
instead. This preserves the existing accepted-input rule while ensuring callers
receive the module's documented validation exception.

The Unicode-digit test for `"²"` was converted from an expected failure into a
normal regression test. The verification command ran 13 tests in 0.010 seconds
with `OK`: all 13 passed and there were no expected failures.

## Milestone 1 maintenance: validation-contract regression coverage

**Status:** Complete

This maintenance update keeps the metadata foundation narrow while reinforcing
its documented error contract. If `urllib.parse.urlsplit` rejects a malformed
source URL, the validator now translates that `ValueError` into
`MetadataValidationError`, matching the behaviour for other invalid metadata.

The test suite now also verifies malformed URL parsing, rejection of
non-mapping input, and reassignment protection on the frozen `PaperMetadata`
model. The duplicate `.gitignore` block was removed without changing any
ignore patterns. The README now identifies the metadata foundation as complete
and accurately states the capabilities that remain unimplemented.

## Milestone 2A: PubMed citation search integration

**Status:** Complete

Milestone 2A adds a framework-neutral, standard-library PubMed client under
`src/ingestion`. It uses ESearch with the NCBI history server and one batched
EFetch request per page. The client supports title, title/abstract, author,
DOI, and PMID searches; an inclusive publication-date filter; total counts;
and pages of up to 20 results. A title search falls back to title/abstract only
when the title-only search has no matches.

Validated results reuse `PaperMetadata` and identify sources as `PMID:<id>`
with an official PubMed record URL. PubMed-only fields are kept in a separate
result model. Abstract text is intentionally ignored, and the milestone makes
no full-text request or persistent cache. Citation parsing follows the approved
primary-year rule: journal `PubDate/Year`, then the first four-digit year in
`MedlineDate` only when `Year` is absent; electronic and history dates are not
substitutes.

The tests use mocked HTTP, clock, and sleeper dependencies only. They cover
search fields, fallback rules, filters, paging, citation mapping, date
precedence, missing abstracts, malformed records and XML, API failures,
configuration, and rate limiting. `docs/PUBMED_INTEGRATION.md` records the
production identification, attribution, disclaimer, and rights-review
requirements before a public deployment.

The configuration follow-up rejects non-finite timeout and request-rate values
and permits only HTTPS URLs on the official NCBI E-utilities host. This keeps
the local rate limiter meaningful and prevents configured identification data
or an API key from being sent to an arbitrary endpoint.

The review-fix pass makes the per-client rate limiter safe for shared-thread
use without holding its lock during HTTP calls. It treats an EFetch document
without citation records after a positive search as a response error, while
retaining individual invalid-record skipping. It also defines the public query
policy: title, title/abstract, and author methods accept plain text; DOI and
PMID methods accept one validated identifier; advanced PubMed query syntax is
out of scope for Milestone 2A.

## Current status

Milestones 1, 1.1, and 2A are complete. The project now has a validated PubMed
citation-search capability and a metadata-validation foundation. It has no
full-text ingestion, abstract display or persistence, evidence retrieval,
generation, evaluation, or user interface capability yet.

## Next steps

Before any new milestone, agree on its scope and acceptance criteria. A
separate Milestone 2 should be approved before implementing any new ingestion
capability.
