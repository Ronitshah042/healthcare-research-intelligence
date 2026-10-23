# Technical Decisions

This record distinguishes decisions already implemented from choices that
remain open. It documents Milestone 1 only.

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
- What Milestone 2 should cover. No Milestone 2 implementation is authorised by
  this document.
