# Content Policy

This policy decides which research text the project may store. It was approved
by the project owner on 2026-11-17 for Phase 2. Anything not allowed here is
not stored. Changing the policy needs the owner's explicit approval and an
update to this file, `AGENTS.md`, `CLAUDE.md`, and `docs/DECISIONS.md`.

## 1. What may be stored

| Content | Stored? | Rule |
| --- | --- | --- |
| PubMed citation metadata (title, authors, journal, year, identifiers, citation details) | Yes | Unchanged since Milestone 2A. |
| **Abstracts from PubMed records** | **No** | Only the `abstract_available` flag is kept. PubMed abstracts are publisher-supplied and carry no reuse licence through PubMed. |
| **Full text of PMC Open Access articles under an allowed licence** | **Yes** | Only from the PMC Cloud Service, only the current version, and only when the licence is on the allowlist (section 3). |
| **The abstract inside an allowed PMC article** | **Yes**, as the article's `abstract` section | It is part of the licensed article, so it carries the same licence and attribution as the rest of the article. |
| Full text under any other licence, with no licence, or with an unrecognised licence | **No** | Rejected and recorded as a rejection (section 5). No text is kept. |
| Retracted articles | **No** | Rejected with the reason `retracted`. |
| Corrected articles, errata, expressions of concern | Yes, **flagged** | See section 6. |
| References, figures, tables, formulas, supplementary files, PDFs, images | **No** | Dropped while parsing; only prose text is kept. |
| Raw API responses | **No** | Only the parsed fields listed in section 4 are stored. |

The two kinds of abstract are deliberately treated differently. A **PubMed
abstract** reaches the project through a PubMed citation record and is never
stored. A **PMC abstract** is part of a CC0 or CC BY licensed article and is
stored as that article's `abstract` section. Never copy one into the other:
a stored PMC abstract must not be attached to the PubMed citation record, and
a paper with no allowed PMC article has no stored abstract.

## 2. Allowed source

Only the **PubMed Central Open Access subset**, retrieved through the services
NCBI permits for automated retrieval. Other PMC content, other publishers'
websites, and scraping are not allowed.

| Purpose | Host (HTTPS only) | Request |
| --- | --- | --- |
| Find open-access PMC articles for a query | `eutils.ncbi.nlm.nih.gov` | `esearch.fcgi`, `db=pmc`, query plus `AND open access[filter]` |
| Link PMID, PMCID and DOI; find the current version | `pmc.ncbi.nlm.nih.gov` | `/tools/idconv/api/v1/articles/` with `ids` (up to 200, all one type), `idtype` (`pmid`, `pmcid`, or `doi`), `versions=yes`, `format=json`, `tool`, `email` |
| Licence and status of one article version | `pmc-oa-opendata.s3.amazonaws.com` | `GET /metadata/PMC<n>.<version>.json` |
| Full text of one article version | `pmc-oa-opendata.s3.amazonaws.com` | `GET /PMC<n>.<version>/PMC<n>.<version>.xml` (JATS XML) |

- The PMC client accepts only these hosts, over HTTPS. The PubMed client stays
  restricted to `eutils.ncbi.nlm.nih.gov`.
- `tool` and `email` (and the API key, for E-utilities) go only to NCBI hosts,
  never to the Amazon S3 host.
- Requests use the same rate limiting, bounded retries, and error redaction as
  the PubMed client: no request URL, credential, or server error text appears
  in an exception or log.
- The licence is read from the version's metadata file **before** the article
  text is downloaded, so text under a rejected licence is never fetched.

## 3. Licences

Configured with the `HRI_ALLOWED_LICENCES` environment variable, a
comma-separated list. **Default, and the strictest setting: `CC0,CC BY`.**

- The licence comes from `license_code` in the PMC Cloud metadata file, for
  example `CC BY`.
- Comparison ignores letter case and repeated spaces, and nothing else. `CC BY`
  allows `CC BY` only, not `CC BY-SA` or `CC BY-NC`.
- Always rejected under the default: `CC BY-NC`, `CC BY-ND`, `CC BY-NC-ND`,
  `CC BY-NC-SA`, `CC BY-SA`, `TDM` (author manuscripts available for text
  mining only), a missing licence, and any value not on the list.
- Widening the list is a policy change and needs the owner's approval.

## 4. Stored with every document

| Field | Meaning |
| --- | --- |
| `pmcid` and `pmcid_version` | For example `PMC10009402` and `PMC10009402.1`; the version is the current one according to the ID converter |
| `pmid`, `doi` | When the article has them; used to link to stored PubMed papers |
| `licence` | The `license_code` string exactly as received |
| `source_url` | The HTTPS URL of the article XML that was parsed |
| `retrieved_at` | UTC date and time of retrieval |
| `attribution` | Text naming the article citation, its licence, and its PMC source, to be shown wherever the text is shown |
| `notice_flags` | Correction and concern flags (section 6) |

Every chunk of text keeps its document, section name, position, and licence.

## 5. Rejections

A rejected article is recorded in `fulltext_rejections` with its PMCID,
version (when known), licence string (when known), reason, and date, and a
warning is logged naming only the PMCID and reason. **No text is stored for a
rejected article.**

| Reason | Meaning |
| --- | --- |
| `licence` | The licence is not on the allowlist, or is missing. |
| `retracted` | The article is marked retracted, or the XML links to a retraction. |
| `not_open_access` | The article is not in the PMC Open Access subset. |
| `current_version_unavailable` | The current version is not in the Open Access bucket. |

## 6. Corrections and expressions of concern

Articles that have been corrected, carry an erratum, are subject to an
expression of concern, or are themselves such a notice are stored with
`notice_flags` so a UI or answer generator can warn about them.

Flags are read from the article XML: the article type (`correction`,
`retraction`, `expression-of-concern`) and `related-article` links of type
`correction-forward`, `corrected-article`, `partial-retraction`, or any type
mentioning concern. PMC rarely tags forward links, so **a missing flag does
not prove an article is uncorrected**.

## 7. Re-checking stored articles

Articles can be retracted after they are stored. `hri recheck-fulltext` (the
`recheck_fulltext` service) re-reads the current version and metadata of
every stored document and **deletes any that are now retracted**, recording
the rejection. Every future rebuild of a data bundle (Phase 6) must run it
first.

## 8. Attribution and reuse

CC BY requires credit to the authors and source. Every stored document keeps
its attribution text, and any display of stored text or of an answer quoting
it must show that attribution. PubMed metadata keeps `PUBMED_ATTRIBUTION` as
before.
