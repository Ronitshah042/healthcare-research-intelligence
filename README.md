# Healthcare Research Intelligence Platform

An AI-powered research assistant designed to retrieve, analyse, and
summarise scientific literature with traceable evidence and citations.

## Problem Statement

Scientific literature is growing rapidly, making it difficult to find
relevant studies, compare findings, and identify gaps in existing evidence.
This project aims to make literature exploration more efficient while
keeping generated answers grounded in retrieved research.

## Planned Features

- Search scientific literature from public research sources
- Retrieve relevant passages using hybrid search
- Generate evidence-grounded answers with citations
- Compare findings across research papers
- Highlight conflicting evidence and research gaps
- Abstain when the available evidence is insufficient
- Evaluate retrieval quality and answer grounding

## Planned Technology Stack

- Python
- PubMed and Europe PMC APIs
- Natural language processing and embeddings
- Keyword and vector-based retrieval
- Large language models (LLMs)
- FastAPI
- Streamlit
- unittest (Python standard library)

The final technology choices will be confirmed during implementation.

## Project Structure

```text
src/
├── ingestion/    PubMed client, pipeline, SQLite storage, saved lists
├── hri/          service layer, export, and the hri command line
├── retrieval/
├── generation/
└── evaluation/
tests/
data/
├── raw/
└── processed/
docs/
```

## Setup

Requirements: Python 3.9 or newer. The project has no third-party runtime
dependencies (`requirements.txt` and `pyproject.toml` both list none).

```bash
git clone https://github.com/Ronitshah042/healthcare-research-intelligence.git
cd healthcare-research-intelligence

# Optional but recommended: install the `hri` command into a virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Run the full test suite (no network access or configuration needed)
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Without the install step, replace `hri` with `PYTHONPATH=src python3 -m hri`
in the commands below. Use `python3`; a plain `python` command may not exist
on your system.

Live PubMed requests (`hri ingest`) need environment variables. Copy
`.env.example` to `.env`, fill in your values, and load it with
`set -a; source .env; set +a`. Everything else works offline, and the tests
never call live services.

## Command-line usage

```bash
# Fetch up to 200 PubMed citations and save them locally
hri ingest --query "type 2 diabetes" --mode title_abstract --max 200 --year-from 2018

# Search what is stored: keyword (default), fuzzy, or boolean
hri search "metformin AND cardiovascular" --type boolean --year-from 2020 --page 1
hri search "diabetis" --type fuzzy

# One paper's stored metadata, by PMID or DOI
hri show 33301246

# Totals, papers per year, DOI and abstract counts
hri stats

# Saved lists
hri lists create "To read"
hri lists add "To read" 33301246
hri lists show "To read"
hri lists list
hri lists remove "To read" 33301246
hri lists export "To read" --format csv --output to-read.csv
hri lists export "To read" --format citations
```

`hri ingest` reports how many records PubMed matched and how many were
fetched, saved, duplicated, skipped as invalid, or skipped for conflicting
identifiers. Modes are `title`, `title_abstract`, `author`, `doi`, and `pmid`;
`--max` goes up to 1000. Run `hri --help` or `hri <command> --help` for every
option.

The database is `data/hri.sqlite3` in the current folder (ignored by Git).
Set `HRI_DB_PATH` or pass `--db PATH` to use another file; `--db` wins.

The command line is a thin layer over `hri.services`, a set of plain Python
functions that return typed results. A UI or web API should call those same
functions; [docs/SERVICE_API.md](docs/SERVICE_API.md) is their contract.

## Responsible Use

This project is intended for scientific literature research and educational
purposes. It is not a medical diagnostic or treatment tool and is not
clinically validated. Answers must be traceable to their supporting sources,
and limitations must be communicated clearly.

Only publicly accessible research materials permitted by their applicable
licences and terms will be used.

## Development Status

**Status:** Phase 1 complete — PubMed ingestion, local search, saved lists,
and export are usable end to end from the `hri` command line through a
UI-agnostic service layer. There is no user interface or web API yet.

The project now provides validated PubMed citation search through NCBI
E-utilities, including title, title/abstract, author, DOI, and PMID search,
year filters, pagination, and links to original PubMed records. Milestone 2B
adds process-shared rate scheduling, bounded transient-failure retries, and
safe NCBI error handling. It remains a small research-support foundation: it
does not retrieve full text, display or persist abstracts, provide clinical
advice, generate answers, or provide a user interface.

A small ingestion search pipeline now accepts one typed search request, routes
it to the matching PubMed client method, and returns a consistent
metadata-only result. It standardises citation fields and removes identifier
duplicates without guessing that unrelated partial records are the same paper.
It reports whether PubMed returned an abstract without retaining its text. Its
source total remains separate from the current page's valid, deduplicated
record count. It is a local library component rather than a web API or user
interface.

`PaperStore` saves standardised citations in a local SQLite file whose path the
caller chooses. It matches papers only by PMID or DOI, refuses to merge
contradictory identifiers, and supports local keyword, fuzzy-title, and
Boolean (`AND`/`OR`/`NOT`, parentheses, quoted phrases) search over titles,
authors, and journals, with year and abstract-availability filters. It stores
citation metadata only, never abstract text.

PubMed-derived results must visibly attribute NLM/NIH and link to the NCBI
disclaimer. Live search results and papers read back from `PaperStore` both
carry this attribution in an `attribution` field. This independent project is not affiliated with or endorsed by
NCBI, NLM, NIH, or HHS. See [the PubMed integration guide](docs/PUBMED_INTEGRATION.md)
for configuration, source-use boundaries, and operational requirements.
