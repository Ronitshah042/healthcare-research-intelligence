"""Pieces shared by the Bento Vitals pages: notices, attribution, tiles, session state.

Pages call only ``hri.services``. ``MANIFEST`` is set by ``streamlit_app.py``
from the verified data bundle before a page runs.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import streamlit as st

from hri import services

PAPER_PAGE = "app_pages/paper.py"
ASK_PAGE = "app_pages/ask.py"
SAVED_PAGE = "app_pages/saved.py"

NOT_ADVICE = "Research aid — not medical advice."
PRIVACY_NOTICE = (
    "Questions go to Google’s Gemini API (free tier), which may use them to improve "
    "its products. Don’t enter personal or patient information."
)
# Badge colours are set in .streamlit/config.toml to the mint, sand and peach tiles.
_CHIP_COLOURS = ("green", "yellow", "orange", "blue")

MANIFEST: dict = {}
_STYLE = Path(__file__).with_name("style.css")


def page_style() -> None:
    """Load the tile styles (our own file); each page calls it after navigation."""
    st.markdown(f"<style>{_STYLE.read_text()}</style>", unsafe_allow_html=True)


@contextmanager
def tile(kind: str, name: str):
    """A rounded tile; ``kind`` is hero, mint, sand, peach or white (style.css)."""
    with st.container(key=f"tile-{kind}-{name}"):
        yield


def chip(number: int) -> str:
    return f":{_CHIP_COLOURS[(number - 1) % len(_CHIP_COLOURS)]}-badge[{number}]"


def identifier(paper: services.StoredPaper) -> str:
    return paper.pmid or paper.doi or ""


def paper_meta(paper: services.StoredPaper) -> str:
    parts = [paper.journal, str(paper.publication_year) if paper.publication_year else None,
             f"PMID {paper.pmid}" if paper.pmid else f"doi:{paper.doi}"]
    return " · ".join(part for part in parts if part)


def paper_row(number: int, paper: services.StoredPaper) -> None:
    """A numbered result that links to the paper's details page."""
    with st.container(horizontal=True, vertical_alignment="top", gap="small"):
        st.markdown(chip(number), width="content")
        with st.container(gap=None):
            st.page_link(PAPER_PAGE, label=paper.title, query_params={"id": identifier(paper)})
            st.caption(paper_meta(paper))


def pubmed_attribution(attribution: services.PubMedAttribution | None) -> None:
    """The four PubMed attribution fields, as SERVICE_API.md requires."""
    if attribution is not None:
        st.caption(
            f"[{attribution.source_name}]({attribution.source_url}) "
            f"{attribution.independent_service_notice} "
            f"[NCBI policies and disclaimer]({attribution.disclaimer_url})."
        )


def footer() -> None:
    st.caption(f"**{NOT_ADVICE}** " + MANIFEST.get(
        "attribution", "Citations: PubMed, U.S. National Library of Medicine (NLM)."))


def saved_lists() -> services.SessionSavedLists:
    """This visitor's lists, kept in memory for the session only."""
    if "saved_lists" not in st.session_state:
        st.session_state.saved_lists = services.SessionSavedLists()
    return st.session_state.saved_lists


def visitor_quota() -> services.VisitorQuota:
    if "visitor_quota" not in st.session_state:
        st.session_state.visitor_quota = services.VisitorQuota()
    return st.session_state.visitor_quota
