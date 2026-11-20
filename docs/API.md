# REST API

A FastAPI interface over the same service functions the `hri` command line
uses (`docs/SERVICE_API.md`). It is kept in the repository as the documented
REST interface; **it is not part of the Streamlit deployment**.

```bash
pip install -e '.[api]'              # Python 3.11 or newer
uvicorn api.app:app --port 8000      # interactive docs at http://localhost:8000/docs
```

The database is `HRI_DB_PATH` (default `data/hri.sqlite3`). Set
`HRI_DB_READ_ONLY=1` to serve a read-only data bundle; `/ingest` and `/index`
then fail with `storage_error`.

| Method | Path | Purpose | Auth |
| --- | --- | --- | --- |
| GET | `/health` | Liveness and database counts | none |
| GET | `/search` | Search stored citations | none |
| GET | `/papers/{id}` | One stored paper by PMID or DOI | none |
| GET | `/stats` | Citation and full-text counts | none |
| POST | `/ask` | Answer from stored passages, with checked citations | none |
| POST | `/ingest` | Fetch PubMed citations and save them | admin |
| POST | `/index` | Embed stored passages for meaning-based search | admin |

**Admin endpoints** need `Authorization: Bearer <HRI_ADMIN_TOKEN>`. The token
is compared in constant time. When `HRI_ADMIN_TOKEN` is not set, both
endpoints answer `503 admin_disabled`.

**Attribution:** PubMed-derived results carry `attribution`; a client must
display it. Answers carry `notice` (which AI provider received the question)
and `disclaimer`; a client must display both.

## Errors

Every error has the same shape:

```json
{"error": {"code": "not_found", "message": "No stored paper has that identifier."}}
```

| Status | `code` | When |
| --- | --- | --- |
| 400 | `invalid_request` | A service rejected an argument (bad identifier, malformed Boolean query, ...) |
| 422 | `invalid_request` | The request failed validation (missing or out-of-range field); `message` names the field |
| 401 | `unauthorized` | Admin token missing or wrong |
| 404 | `not_found` | No stored paper has that identifier |
| 500 | `storage_error` | The database could not be read or written (details are not exposed) |
| 502 | `upstream_error` | PubMed or PMC failed |
| 503 | `not_configured`, `embedder_unavailable`, `admin_disabled` | Missing NCBI settings, embedding model unavailable, or admin token not set |

An AI provider being busy is **not** an error: `/ask` returns `200` with
`abstained: true` and `abstain_reason: "busy"`.

## Examples

The responses below were produced by the test client against sample data
(the `/ask` answer with citations is illustrative).

### `GET /health`

```json
{"status": "ok", "read_only": false, "papers": 2, "fulltext_articles": 0}
```

### `GET /search?q=paper&page_size=1`

Parameters: `q` (required), `strategy` (`keyword`, `fuzzy`, `boolean`),
`page` (from 1), `page_size` (1 to 100), `year_from`, `year_to`, `abstract`
(`yes` or `no`).

```json
{
  "query": "paper",
  "strategy": "keyword",
  "papers": [
    {
      "pmid": "33301246",
      "doi": null,
      "title": "Paper 33301246",
      "authors": ["Ada Lovelace", "Grace Hopper"],
      "journal": "Example Journal",
      "publication_year": 2021,
      "abstract_available": true,
      "source_url": "https://pubmed.ncbi.nlm.nih.gov/33301246/",
      "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/33301246/",
      "source_name": "PubMed"
    }
  ],
  "total_count": 2,
  "page": 1,
  "page_size": 1,
  "total_pages": 2,
  "attribution": {
    "source_name": "Source: PubMed, U.S. National Library of Medicine (NLM), NIH.",
    "source_url": "https://pubmed.ncbi.nlm.nih.gov/",
    "disclaimer_url": "https://www.ncbi.nlm.nih.gov/home/about/policies/",
    "independent_service_notice": "This independent service is not affiliated with, endorsed by, or sponsored by NCBI, NLM, NIH, or HHS."
  }
}
```

### `GET /papers/33301246`

`{id}` is a PMID or a DOI (`/papers/10.1234/example` works).

```json
{
  "paper": {"pmid": "33301246", "doi": null, "title": "Paper 33301246", "...": "..."},
  "attribution": {"source_name": "Source: PubMed, U.S. National Library of Medicine (NLM), NIH.", "...": "..."}
}
```

### `GET /stats`

```json
{
  "total_papers": 2,
  "papers_by_year": [[2021, 2]],
  "with_doi": 1,
  "without_doi": 1,
  "abstract_available": 2,
  "fulltext_articles": 0,
  "fulltext_passages": 0
}
```

### `POST /ask`

Request: `{"question": "...", "evidence_count": 6}` (`evidence_count` 2 to 10).

When nothing relevant is stored:

```json
{
  "question": "Does metformin affect cognition?",
  "text": "The stored evidence is not sufficient to answer this question reliably.",
  "abstained": true,
  "abstain_reason": "insufficient_evidence",
  "citations": [],
  "invalid_citations": 0,
  "provider": null,
  "model": null,
  "cached": false,
  "search_method": "keyword",
  "notice": null,
  "disclaimer": "Research aid only, not medical advice. This summary of the cited studies is not clinically validated; consult a qualified health professional about any health decision."
}
```

An answered question (illustrative):

```json
{
  "question": "Is metformin associated with cognitive function in older adults with type 2 diabetes?",
  "text": "In the SCOPE cohort, metformin users kept stable MMSE scores over two years while non-users declined slightly [1]; the difference was small [2].",
  "abstained": false,
  "abstain_reason": null,
  "citations": [
    {
      "number": 1,
      "pmcid": "PMC13756269",
      "pmcid_version": "PMC13756269.1",
      "pmid": "43003454",
      "doi": "10.1136/bmjopen-2025-114393",
      "title": "Is metformin use associated with cognitive, physical and cardiovascular outcomes in older adults with type 2 diabetes? ...",
      "section": "discussion",
      "link": "https://pmc.ncbi.nlm.nih.gov/articles/PMC13756269/",
      "licence": "CC BY",
      "attribution": "... Licence: CC BY (...). Source: PubMed Central, ..., retrieved 2026-11-18. Changes: ..."
    }
  ],
  "invalid_citations": 0,
  "provider": "Gemini",
  "model": "gemini-3.5-flash-lite",
  "cached": false,
  "search_method": "hybrid",
  "notice": "Your question and the evidence passages were sent to Gemini (gemini-3.5-flash-lite). On Gemini's free tier, Google may use them to improve its products.",
  "disclaimer": "Research aid only, not medical advice. ..."
}
```

### `POST /ingest` (admin)

Request: `{"query": "metformin", "mode": "title_abstract", "max_records": 20, "year_from": 2018, "year_to": null}`.
Modes: `title`, `title_abstract`, `author`, `doi`, `pmid`; `max_records` 1 to 1000.

```json
{"query": "metformin", "mode": "title_abstract", "total_available": 2, "fetched": 2, "saved": 2, "duplicates": 0, "invalid_skipped": 0}
```

### `POST /index` (admin)

No body. Downloads the embedding model on first use.

```json
{"model_id": "BAAI/bge-small-en-v1.5@aa8f8b0", "embedded": 106, "over_token_limit": 5, "longest_tokens": 523}
```

Without a token:

```json
{"error": {"code": "unauthorized", "message": "A valid admin token is required."}}
```
