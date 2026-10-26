
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
- Pytest

The final technology choices will be confirmed during implementation.

## Project Structure

```text
src/
├── ingestion/
├── retrieval/
├── generation/
└── evaluation/
tests/
data/
├── raw/
└── processed/
docs/
```

## Responsible Use

This project is intended for scientific literature research and educational
purposes. It is not a medical diagnostic or treatment tool and is not
clinically validated. Answers must be traceable to their supporting sources,
and limitations must be communicated clearly.

Only publicly accessible research materials permitted by their applicable
licences and terms will be used.

## Development Status

**Status:** Milestone 2A complete — PubMed citation search integration

The project now provides validated PubMed citation search through NCBI
E-utilities, including title, title/abstract, author, DOI, and PMID search,
year filters, pagination, and links to original PubMed records. It remains a
small research-support foundation: it does not retrieve full text, display or
persist abstracts, provide clinical advice, generate answers, or provide a
user interface.

PubMed-derived results must visibly attribute NLM/NIH and link to the NCBI
disclaimer. This independent project is not affiliated with or endorsed by
NCBI, NLM, NIH, or HHS. See [the PubMed integration guide](docs/PUBMED_INTEGRATION.md)
for configuration, source-use boundaries, and operational requirements.
