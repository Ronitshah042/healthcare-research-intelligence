# Deployment: Streamlit Community Cloud

The app runs for free on Streamlit Community Cloud. It reads a **read-only
data bundle** (one SQLite file plus a manifest) published as assets of the
GitHub Release tagged `data-bundle`, and keeps everything it writes at run
time in memory.

> `streamlit_app.py` is currently a **placeholder page** that only proves the
> deployment works (settings, bundle, self-check, model, `get_stats`,
> `search_papers`). The real UI is being designed separately and should call
> only `hri.services` (`docs/SERVICE_API.md`).

## How it fits together

```text
GitHub Actions "Build data bundle" (weekly / manual)
  ingest -> fetch-fulltext (CC0, CC BY) -> recheck-fulltext -> embed-passages
  -> hri-bundle.sqlite3 + hri-bundle.manifest.json
  -> uploaded to the release "data-bundle"

Streamlit Community Cloud (streamlit_app.py)
  secrets -> environment -> download bundle once, verify SHA-256, cache
  -> self-check (layout version, embedding model, checksum)
  -> HRI_DB_READ_ONLY=1, answers cached in memory, daily cap 50
```

## The data bundle

| | |
| --- | --- |
| Built by | `hri build-bundle --topics topics.json --output dist` |
| Topics | `topics.json`: 5 topics, up to 40 citations and 35 open-access candidates each (about 60% pass the licence check, so about 100 articles) |
| Size | 14.0 MB (first build, 2026-11-20: 174 citations, 96 CC BY articles, 2,392 passages and vectors; 64 candidates rejected by licence, none retracted) |
| Licences | CC0 and CC BY only (CONTENT_POLICY.md section 13) |
| Contents | citations, licensed passages, search index, vectors; no caches, saved lists, rejection records or per-section text |
| Journal mode | rollback (`DELETE`), so it opens read-only and immutable |
| Manifest | build time, layout version, embedding model, counts, licences, retraction re-check, SHA-256, size |
| Hosting | GitHub Release `data-bundle` (not committed, so history stays small) |

## One-time setup

1. **Repository secrets** (GitHub: Settings, Secrets and variables, Actions):
   `PUBMED_NCBI_TOOL`, `PUBMED_NCBI_EMAIL` (your developer contact),
   optionally `PUBMED_NCBI_API_KEY`. Only the bundle workflow uses them.
2. **Build the first bundle:** Actions, "Build data bundle", Run workflow.
   It runs the tests, builds the bundle (about 20 to 40 minutes at NCBI's
   rate limits, plus a one-off model download), and creates the release
   `data-bundle` with the two files. Check the manifest's counts and
   licences.
3. **Create the app** at share.streamlit.io: New app, this repository,
   branch `main`, main file `streamlit_app.py`. Under Advanced settings
   choose **Python 3.12**.
4. **Secrets** (app Settings, Secrets), from `.streamlit/secrets.toml.example`:

   ```toml
   HRI_BUNDLE_URL = "https://github.com/<owner>/<repo>/releases/download/data-bundle/"
   GEMINI_API_KEY = "..."        # optional: AI answers
   GROQ_API_KEY = "..."          # optional: fallback provider
   HRI_LLM_DAILY_REQUEST_CAP = "50"
   ```

   NCBI settings are not needed by the app. Leave out the AI keys to deploy
   search only; questions then return a "busy" answer with a notice.
5. **Deploy.** The first start downloads the bundle and the embedding model
   (about 67 MB from Hugging Face), then the page shows the bundle date and
   counts. Any self-check problem is shown on the page instead of a crash.

## Rebuilding the data

- **Automatically** every Monday 03:00 UTC, or **manually** with Run
  workflow. Each build re-checks retractions first.
- The workflow replaces the two assets of `data-bundle`. A running app keeps
  its cached copy until it restarts (Settings, Reboot app); on start it
  downloads the new bundle because the checksum in the manifest changed.
- To change the collection, edit `topics.json` (JSON, `"format": 1`; up to 20
  topics, 200 citations and 50 full-text articles each) and run the workflow.
- Locally: `pip install -e '.[embeddings]'`, set the `PUBMED_*` variables,
  then `hri build-bundle`. Point a local app at it with
  `HRI_BUNDLE_DIR=dist streamlit run streamlit_app.py`.

## Free-tier limits and how we handle them

| Limit | Effect | How it is handled |
| --- | --- | --- |
| The app **sleeps when idle** (no visits for a while) | The next visitor waits for a cold start | Start-up work is cached once per process (`st.cache_resource`): bundle download and check, model load. The bundle is small and the model is about 67 MB |
| About **1 GB of RAM** | A large model or index would crash the app | bge-small (about 250 MB in use) with ONNX Runtime, no PyTorch; the bundle is read from disk, not loaded into memory; vector search is brute force over about 100 articles' passages |
| **Disk is not persistent** | Anything written is lost on restart | The database is opened read-only (`HRI_DB_READ_ONLY=1`). Saved lists live in the visitor's session (`SessionSavedLists`) with CSV and citation export; the answer cache and daily request count live in process memory. **All of these reset when the app restarts**, so the UI should offer list export |
| **Free AI quotas** (Gemini, Groq) shared by every visitor | One busy visitor could use up the day | App-wide cap of 50 requests a day (`HRI_LLM_DAILY_REQUEST_CAP`), 5 questions per visitor (`VisitorQuota`), cached answers reused; when the cap or a provider refuses, the answer is "busy", not an error |
| **Gemini free tier uses prompts to improve Google's products** | Visitors' questions leave the app | Only CC0/CC BY passages and the question are sent; every answer names the provider; the UI should tell visitors not to enter personal data |
| Public repository, public app | Anyone can read the bundle | It holds only openly licensed text and citation metadata, with attribution |

## Pinned versions

`requirements.txt` pins only fastembed and Streamlit. Everything they install
is pinned for the cloud platform (Linux, Python 3.12) in
`constraints-deploy.txt`, which CI and the bundle workflow install with, so
the tested set and the bundle's build environment match. Streamlit Cloud
itself installs from `requirements.txt` alone, so a new release of a
sub-package could reach the app first. If a deployment breaks after such a
release, add the line `-c constraints-deploy.txt` to the top of
`requirements.txt` (this breaks local installs on Intel Macs, where
onnxruntime 1.32 does not exist). Regenerate the file with the command in its
header whenever `requirements.txt` changes.

## Security notes

- Secrets live only in Streamlit's Secrets box and GitHub's repository
  secrets, never in the repository (`.streamlit/secrets.toml` and `.env` are
  ignored by Git).
- The bundle is downloaded only over HTTPS from `github.com` and
  `*.githubusercontent.com`, including redirects, and is refused unless its
  SHA-256 matches the manifest.
- The REST API (`docs/API.md`) is not deployed; its admin endpoints would
  need `HRI_ADMIN_TOKEN`.
