"""The documented REST interface (FastAPI). Every endpoint calls only ``hri.services``.

Optional install: ``pip install -e '.[api]'`` (Python 3.11+). Run with
``uvicorn api.app:app``. It is kept in the repository as the reference
interface and is not part of the Streamlit deployment. See ``docs/API.md``.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from hri import services

ADMIN_TOKEN_VARIABLE = "HRI_ADMIN_TOKEN"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ---------- response models ----------

class Attribution(BaseModel):
    source_name: str
    source_url: str
    disclaimer_url: str
    independent_service_notice: str


class Paper(BaseModel):
    pmid: str | None
    doi: str | None
    title: str
    authors: list[str]
    journal: str | None
    publication_year: int | None
    abstract_available: bool
    source_url: str | None
    pubmed_url: str | None
    source_name: str | None


class SearchResponse(BaseModel):
    query: str
    strategy: str
    papers: list[Paper]
    total_count: int
    page: int
    page_size: int
    total_pages: int
    attribution: Attribution | None


class PaperResponse(BaseModel):
    paper: Paper
    attribution: Attribution | None


class StatsResponse(BaseModel):
    total_papers: int
    papers_by_year: list[tuple[int | None, int]]
    with_doi: int
    without_doi: int
    abstract_available: int
    fulltext_articles: int
    fulltext_passages: int


class Citation(BaseModel):
    number: int
    pmcid: str
    pmcid_version: str
    pmid: str | None
    doi: str | None
    title: str
    section: str
    link: str
    licence: str
    attribution: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=services.MAX_QUESTION_LENGTH)
    evidence_count: int = Field(default=services.DEFAULT_EVIDENCE, ge=2,
                                le=services.MAX_EVIDENCE)


class AnswerResponse(BaseModel):
    question: str
    text: str
    abstained: bool
    abstain_reason: str | None
    citations: list[Citation]
    invalid_citations: int
    provider: str | None
    model: str | None
    cached: bool
    search_method: str
    notice: str | None
    disclaimer: str


class IngestRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: Literal["title", "title_abstract", "author", "doi", "pmid"] = "title_abstract"
    max_records: int = Field(default=services.DEFAULT_INGEST_RECORDS, ge=1,
                             le=services.MAX_INGEST_RECORDS)
    year_from: int | None = None
    year_to: int | None = None


class IngestResponse(BaseModel):
    query: str
    mode: str
    total_available: int
    fetched: int
    saved: int
    duplicates: int
    invalid_skipped: int


class IndexResponse(BaseModel):
    model_id: str
    embedded: int
    over_token_limit: int
    longest_tokens: int


class HealthResponse(BaseModel):
    status: Literal["ok"]
    read_only: bool
    papers: int
    fulltext_articles: int


# ---------- app ----------

def create_app(database_path: str | Path | None = None) -> FastAPI:
    """Build the app; ``database_path`` defaults to ``HRI_DB_PATH`` or data/hri.sqlite3."""
    app = FastAPI(
        title="Healthcare Research Intelligence API",
        version="0.1.0",
        description="Research aid over PubMed citations and open-access full text. "
        "Not medical advice. PubMed-derived results carry NLM attribution.",
    )
    db = database_path

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, error: ApiError) -> JSONResponse:
        return _error(error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, error: RequestValidationError) -> JSONResponse:
        first = error.errors()[0] if error.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        return _error(422, "invalid_request", f"{where}: {first.get('msg', 'invalid value')}")

    @app.exception_handler(services.InvalidRequestError)
    async def _invalid(request: Request, error: Exception) -> JSONResponse:
        return _error(400, "invalid_request", str(error))

    @app.exception_handler(services.PaperNotFoundError)
    async def _not_found(request: Request, error: Exception) -> JSONResponse:
        return _error(404, "not_found", str(error))

    @app.exception_handler(services.PubMedConfigurationError)
    async def _not_configured(request: Request, error: Exception) -> JSONResponse:
        return _error(503, "not_configured", "PubMed access is not configured on the server.")

    @app.exception_handler(services.PubMedError)
    async def _upstream(request: Request, error: Exception) -> JSONResponse:
        return _error(502, "upstream_error", str(error))

    @app.exception_handler(services.EmbedderUnavailableError)
    async def _no_embedder(request: Request, error: Exception) -> JSONResponse:
        return _error(503, "embedder_unavailable", str(error))

    @app.exception_handler(services.PaperStorageError)
    async def _storage(request: Request, error: Exception) -> JSONResponse:
        # Storage messages are written to be safe, but keep server details out.
        return _error(500, "storage_error", "The database could not be read or written.")

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        stats = services.get_stats(database_path=db)
        fulltext = services.get_fulltext_stats(database_path=db)
        return HealthResponse(
            status="ok", read_only=services.read_only_from_environment(),
            papers=stats.total_papers, fulltext_articles=fulltext.documents,
        )

    @app.get("/search", response_model=SearchResponse)
    def search(
        q: str = Query(min_length=1, description="Search text"),
        strategy: Literal["keyword", "fuzzy", "boolean"] = "keyword",
        page: int = Query(1, ge=1),
        page_size: int = Query(services.DEFAULT_PAGE_SIZE, ge=1, le=services.MAX_PAGE_SIZE),
        year_from: int | None = None,
        year_to: int | None = None,
        abstract: Literal["yes", "no"] | None = None,
    ) -> SearchResponse:
        result = services.search_papers(
            q, strategy=strategy, page=page, page_size=page_size, year_from=year_from,
            year_to=year_to, abstract_available=None if abstract is None else abstract == "yes",
            database_path=db,
        )
        return SearchResponse(
            query=result.query, strategy=result.strategy,
            papers=[_paper(p) for p in result.papers], total_count=result.total_count,
            page=result.page, page_size=result.page_size, total_pages=result.total_pages,
            attribution=_attribution(result.attribution),
        )

    @app.get("/papers/{identifier:path}", response_model=PaperResponse)
    def paper(identifier: str) -> PaperResponse:
        found = services.get_paper(identifier, database_path=db)
        return PaperResponse(paper=_paper(found), attribution=_attribution(found.attribution))

    @app.get("/stats", response_model=StatsResponse)
    def stats() -> StatsResponse:
        papers = services.get_stats(database_path=db)
        fulltext = services.get_fulltext_stats(database_path=db)
        return StatsResponse(
            total_papers=papers.total_papers, papers_by_year=list(papers.papers_by_year),
            with_doi=papers.with_doi, without_doi=papers.without_doi,
            abstract_available=papers.abstract_available,
            fulltext_articles=fulltext.documents, fulltext_passages=fulltext.chunks,
        )

    @app.post("/ask", response_model=AnswerResponse)
    def ask(body: AskRequest) -> AnswerResponse:
        answer = services.ask_question(
            body.question, evidence_count=body.evidence_count, database_path=db
        )
        return AnswerResponse(
            question=answer.question, text=answer.text, abstained=answer.abstained,
            abstain_reason=answer.abstain_reason,
            citations=[Citation(**vars(c)) for c in answer.citations],
            invalid_citations=answer.invalid_citations, provider=answer.provider,
            model=answer.model, cached=answer.cached, search_method=answer.search_method,
            notice=answer.notice, disclaimer=answer.disclaimer,
        )

    @app.post("/ingest", response_model=IngestResponse, dependencies=[Depends(_require_admin)])
    def ingest(body: IngestRequest) -> IngestResponse:
        result = services.ingest(
            body.query, mode=body.mode, max_records=body.max_records,
            year_from=body.year_from, year_to=body.year_to, database_path=db,
        )
        return IngestResponse(
            query=result.query, mode=result.mode.value, total_available=result.total_available,
            fetched=result.fetched, saved=result.saved, duplicates=result.duplicates,
            invalid_skipped=result.invalid_skipped,
        )

    @app.post("/index", response_model=IndexResponse, dependencies=[Depends(_require_admin)])
    def index() -> IndexResponse:
        result = services.embed_passages(database_path=db)
        return IndexResponse(**vars(result))

    return app


def _require_admin(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get(ADMIN_TOKEN_VARIABLE, "").strip()
    if not expected:
        raise ApiError(503, "admin_disabled", "Admin endpoints are disabled on this server.")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise ApiError(401, "unauthorized", "A valid admin token is required.")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _paper(paper) -> Paper:
    return Paper(
        pmid=paper.pmid, doi=paper.doi, title=paper.title, authors=list(paper.authors),
        journal=paper.journal, publication_year=paper.publication_year,
        abstract_available=paper.abstract_available, source_url=paper.source_url,
        pubmed_url=paper.pubmed_url, source_name=paper.source_name,
    )


def _attribution(attribution) -> Attribution | None:
    return None if attribution is None else Attribution(**vars(attribution))


app = create_app()
