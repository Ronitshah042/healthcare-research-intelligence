# CLAUDE.md

@AGENTS.md

`AGENTS.md` holds the project rules and takes precedence. This file adds the
commands and code facts needed to work here.

## Before changing anything
- Present a review first (goal, files to change, proposed content or diff), then
  wait for approval before saving, editing, or running anything that changes state.
- Implement only the approved milestone. No next milestone is approved until the
  user says so.
- Never commit or push without explicit permission.

## Commands
There is no `python` command here; use `python3`. No install step is needed.

```bash
# Full test suite
PYTHONPATH=src python3 -m unittest discover -s tests -v

# One test module
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_storage.py' -v

# Whitespace check on pending changes
git diff --check
```

The suite fails with `No module named 'ingestion'` if `PYTHONPATH=src` is missing.
"Retrying a transient PubMed request failure." in test output is expected logging.

## Current state
Phases 1 to 6 are built: PubMed citations, licensed PMC full text, hybrid
passage search, cited answers, the evaluation harness, and deployment
preparation (read-only data bundle, Streamlit placeholder, FastAPI). The UI
is not built; the owner designs it. `src/ingestion` holds the data layers,
`src/retrieval`, `src/generation` and `src/evaluation` the layers described
below, and `src/hri` the application layer. See `docs/PROJECT_LOG.md` for
history.

Dependency direction, lowest layer first:

| Module | Responsibility |
|---|---|
| `http.py` | Shared rate limiting, retries, and error redaction for every API client |
| `identifiers.py` | Shared DOI-prefix and PMID leading-zero clean-up; imports nothing from the project |
| `metadata.py` | `PaperMetadata` and validation; imports nothing from the project |
| `pubmed_config.py` | `PubMedClientConfig` and environment variables |
| `pubmed.py` | `PubMedClient`: query validation, XML mapping (HTTP via `http.py`) |
| `pipeline.py` | `SearchPipeline`: thin routing, `StandardisedPaper`, deduplication |
| `schema.py` | Versioned SQLite layout and upgrades (`PRAGMA user_version`) |
| `storage.py` | `PaperStore`: SQLite persistence, local search, and statistics |
| `saved_lists.py` | `SavedListStore`: named lists of stored papers |
| `licence_policy.py` | `LicencePolicy`: the `HRI_ALLOWED_LICENCES` allowlist |
| `pmc_client.py` | `PmcClient`: PMC search, ID converter, Cloud metadata and XML |
| `fulltext.py` | Parse JATS XML into canonical sections; drop references, figures, tables |
| `chunking.py` | Split sections into ~400-token passages, never across sections |
| `fulltext_store.py` | `FullTextStore`: documents, sections, chunks, rejections, FTS5 passage search |

`src/hri` sits on top and must stay free of business logic in the CLI:

| Module | Responsibility |
|---|---|
| `config.py` | Database path from `--db`, `HRI_DB_PATH`, or the default |
| `export.py` | CSV and plain-citation rendering |
| `errors.py` | `InvalidRequestError`, shared by the service modules |
| `services.py` | UI-agnostic functions returning dataclasses; see `docs/SERVICE_API.md` |
| `fulltext_services.py` | Full-text fetch, recheck, and stats; re-exported by `services` |
| `answer_services.py` | `ask_question`; re-exported by `services` |
| `eval_services.py` | `run_eval`, `export_verdicts`; re-exported by `services` |
| `bundle_services.py` | `build_bundle`, `fetch_bundle`, `check_deployment` for the read-only data bundle |
| `session_state.py` | `SessionSavedLists`, `VisitorQuota`: in-memory state for the deployed app |
| `app_config.py` | Streamlit secrets or environment variables into the environment |
| `retrieval_services.py` | Passage search (hybrid or keyword), embedding, model preparation; re-exported by `services` |
| `cli.py` | `hri` argparse commands; imports only `services` |

`src/generation` holds `llm.py` (Gemini, Groq, fallback; standard-library
HTTP), `cache.py` (answer cache, daily cap), and `rag.py` (prompt, citation
checks, `Answer`). It may import `ingestion` and `retrieval`, never `hri`.

`src/api/app.py` is the FastAPI interface (optional `.[api]`, Python 3.11+,
not deployed); it calls only `hri.services`. `streamlit_app.py` is a
placeholder page; the owner designs the UI.

`src/evaluation` holds `metrics.py` (pure metric functions), `judge.py`
(AI judge of cited claims) and `runner.py` (question sets, report, spot-check
sheet). It may import `ingestion`, `retrieval` and `generation`, never `hri`.
Question sets and the judge prompt live in `eval/`.

`src/retrieval` sits between them: `embeddings.py` (the pinned model, the
optional `fastembed` adapter, vector encoding) and `hybrid.py` (vector search
and reciprocal rank fusion). It may import `ingestion`, never `hri`.

Public names are exported from `src/ingestion/__init__.py`; add new ones there.

## Rules that are easy to break
**Source content**
- `docs/CONTENT_POLICY.md` is the source of truth for stored text. Read it
  before touching ingestion of any text.
- PubMed abstracts are never kept: `PaperMetadata.abstract` stays `None` for
  PubMed records, and only the `abstract_available` boolean is stored.
- Full text may be stored only for PMC Open Access articles whose
  `license_code` is on `HRI_ALLOWED_LICENCES` (default `CC0,CC BY`), only the
  current version, and only from the PMC Cloud Service. The licence is checked
  before the text is downloaded. Retracted articles are rejected; corrections
  and concerns are stored with `notice_flags`.
- The abstract inside an allowed PMC article is stored as its `abstract`
  section. Never copy it onto the PubMed citation record.
- Rejected articles leave only a `fulltext_rejections` row, never text.
- Never store raw API responses. Widening the licence list or adding a text
  source needs the owner's explicit approval.
- PubMed-derived results must keep `PUBMED_ATTRIBUTION`; any UI must display it.
  Stored papers derive it from `source_name`, so keep that set to `PUBMED_SOURCE_NAME`.

**Secrets and requests**
- The PubMed client's `base_url` is restricted to HTTPS on
  `eutils.ncbi.nlm.nih.gov`. Do not loosen this; tests inject a transport
  instead of using a mock server.
- The PMC client has a fixed HTTPS allowlist: `eutils.ncbi.nlm.nih.gov`,
  `pmc.ncbi.nlm.nih.gov` (ID converter), and `pmc-oa-opendata.s3.amazonaws.com`.
  `tool`, `email`, and the API key are sent to NCBI hosts only, never to S3.
- Hugging Face is contacted only by `retrieval.embeddings.prepare`, to download
  the pinned model revision; never for searches, and never with user data.
  Changing the model, revision, or file hash needs the owner's approval and an
  update to DECISIONS.md and CONTENT_POLICY.md.
- AI providers: only `generativelanguage.googleapis.com` and `api.groq.com`,
  keys in headers only. Only the question and CC0/CC BY passages are sent.
  Never log prompts or keys; the cache stores replies, not prompts.
- `ask_question` must never raise because a provider failed; it returns a
  "busy" `Answer`. Every `Answer` carries `DISCLAIMER`.
- Do not put NCBI `<ERROR>` text, API keys, or request URLs in exceptions or logs.
- The rate-limit scheduler is shared per process and keeps only a digest of the
  API key. Never hold its lock during HTTP calls. Every retry takes a new slot.

**Search behaviour**
- `search_title` is the only method with a fallback (`[ti]`, then `[tiab]` once).
- PubMed plain-text methods reject query syntax. Advanced queries need approval.
- `total_count` is PubMed's overall count; `normalised_count` is the current page.

**Application layer**
- `cli.py` calls only `hri.services`. A future UI or API must do the same.
- Service functions return typed objects and never print. Changing a signature,
  result field, or error means updating `docs/SERVICE_API.md` in the same change.
- No UI design in this repository: the owner designs the UI. Only the
  placeholder `streamlit_app.py` (stats and citation search) exists.
- The deployed app opens its bundle read-only (`HRI_DB_READ_ONLY=1`). Never
  rely on writing at run time there; use process memory or the session.
- Public bundles contain only CC0 and CC BY text (`BUNDLE_LICENCES`), and
  every build re-checks retractions first.
- `ruff check src tests streamlit_app.py` must pass (rules E4, E7, E9, F, I).
- `requirements.txt` (Streamlit runtime) must equal the `embeddings` and
  `app` extras, and `requirements-dev.txt` the `api` and `dev` extras; the
  core `dependencies` list stays empty.
  `constraints-embeddings.txt` pins every package the embeddings extra installs
  and must carry the same fastembed version; regenerate it with `pip freeze`
  from a tested Python 3.13 environment.
- Changing the database layout means a new step in `schema.py` and a higher
  `SCHEMA_VERSION`, with a test that upgrades the previous layout.

**Paper identity**
- Match papers only on PMID or DOI, never on title similarity.
- `PaperStore.save` raises `PaperIdentityConflictError` on contradictory IDs.
- `save` fills in a missing identifier but overwrites all other fields with the
  incoming values, including `None`. The exception is `source_name`: an incoming
  `None` keeps the stored source, so attribution is never lost on update.
- Publication year is `PubDate/Year`, then the first year in `MedlineDate`. Never
  substitute `ArticleDate` or history dates.

## Code conventions
- Standard library only in the core. Approved optional extras: `embeddings`
  (fastembed, imported only in `retrieval/embeddings.py`), `app` (streamlit,
  only in `streamlit_app.py` and tests), `api` (fastapi, pydantic, uvicorn,
  only in `src/api/`), `dev` (ruff, httpx). The core must work without any
  of them. Adding any other dependency needs approval.
- The minimum Python version is 3.11 (`requires-python`); CI tests 3.11 and
  3.12, the version Streamlit Community Cloud runs.
- Use frozen dataclasses for records and specific exception classes for errors.
- Use British spelling in new names (`normalise`); `storage.py` is an older exception.
- Library code takes local data paths from the caller. The one approved default
  is the `hri` application database, `data/hri.sqlite3` (overridden by
  `HRI_DB_PATH` or `--db`), which Git ignores. Never commit databases or downloads.

## Tests
- Use `unittest`. Tests must never call live services or sleep for real time.
- Inject `transport`, `clock`, and `sleeper` into `PubMedClient` and `PmcClient`;
  use a fake client for pipeline and service tests and a temporary directory for
  storage tests. PMC tests use the recorded fixtures in `tests/fixtures/pmc/`.
- API and Streamlit tests skip when FastAPI or Streamlit is not installed,
  so the core-only CI step still passes; CI runs them on 3.11 and 3.12.
- Evaluation tests (`tests/test_evaluation.py`) use fixed data, `FakeLLM`
  and `FakeEmbedder`. Spot-check sheets (`eval/spotcheck-*.csv`) hold
  article text and stay uncommitted; commit only `eval/verdicts-*.csv`
  (`hri eval-verdicts`), which has passage IDs instead of text.
- Answer tests use `FakeLLM` (`tests/test_answer.py`) and a fake POST
  transport; they never contact an AI provider.
- Embedding tests use `FakeEmbedder` (`tests/test_retrieval.py`) and set
  `HRI_MODEL_CACHE` to a temporary folder; they never import a real model or
  download anything.
- Reset shared limiter state with `PubMedClient._reset_process_rate_limiters_for_tests()`.
- Add a regression test with every bug fix.

## Docs to update with each milestone
- `docs/PROJECT_LOG.md`: always, with the exact test result. Never claim tests passed
  unless they ran.
- `docs/DECISIONS.md`: for design decisions and trade-offs.
- `docs/PUBMED_INTEGRATION.md`: when the PubMed client's behaviour changes.
- `docs/LEARNING_NOTES.md`: beginner-friendly explanations of new concepts.
- `README.md`: when project capabilities or status change.
