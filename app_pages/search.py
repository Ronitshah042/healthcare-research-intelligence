"""Search: the stored PubMed citations, with the collection's real counts."""

from __future__ import annotations

import streamlit as st

from app_pages import common
from hri import services

common.page_style()

PAGE_SIZE = 10
STRATEGY_LABELS = {"keyword": "Keyword", "fuzzy": "Fuzzy title", "boolean": "Boolean"}

stats = services.get_stats()
fulltext = services.get_fulltext_stats()

hero, side = st.columns([8, 4], gap="medium")
with hero, common.tile("hero", "search"):
    st.markdown(":green-badge[PubMed + open-access evidence]")
    st.title("Ask medicine’s research a question.")
    with st.form("search", border=False):
        box, go = st.columns([5, 1], vertical_alignment="bottom")
        query = box.text_input(
            "Search stored citations", value=st.session_state.get("query", ""),
            placeholder="For example: SGLT2 inhibitors heart failure")
        if go.form_submit_button("Search", type="primary", width="stretch"):
            st.session_state.query = query.strip()
            st.session_state.result_page = 1
    strategy = st.segmented_control(
        "Match", list(STRATEGY_LABELS), format_func=STRATEGY_LABELS.get,
        default="keyword", required=True, key="strategy")
    st.caption("Searches titles, authors and journals of the stored PubMed citations. "
               "Nothing typed here is sent to an AI.")

with side:
    left, right = st.columns(2)
    with left, common.tile("mint", "citations"):
        st.metric("Citations", stats.total_papers)
    with right, common.tile("sand", "open-access"):
        st.metric("Open access", fulltext.documents)
    with common.tile("white", "safety"):
        st.markdown("**Safety first**")
        st.write("Answers use only open-access text, every claim is cited, and the tool "
                 "says so when the evidence isn’t enough.")
        st.markdown(f"**{common.NOT_ADVICE}**")

results_column, answer_column = st.columns([7, 5], gap="medium")
query = st.session_state.get("query", "")
with results_column, common.tile("white", "results"):
    if not query:
        st.subheader("Search the stored citations")
        topics = len(common.MANIFEST.get("topics", []))
        st.write(f"{stats.total_papers} PubMed citations"
                 + (f" on {topics} topic{'s' if topics != 1 else ''}" if topics else "")
                 + ". Try a drug, a condition, an author or a journal.")
    else:
        page = st.session_state.get("result_page", 1)
        try:
            results = services.search_papers(
                query, strategy=strategy, page=page, page_size=PAGE_SIZE)
        except services.InvalidRequestError as error:
            st.error(str(error))
            results = None
        if results is not None:
            st.subheader(f"{results.total_count} citation{'s' if results.total_count != 1 else ''}")
            years = [p.publication_year for p in results.papers if p.publication_year]
            st.caption(f"“{query}” · {STRATEGY_LABELS[strategy].lower()} match"
                       + (f" · {min(years)}–{max(years)}" if years else ""))
            if not results.papers:
                st.write("No stored citation matches. Try fewer or different words, "
                         "or Fuzzy title for spelling differences.")
            for number, paper in enumerate(results.papers, start=(page - 1) * PAGE_SIZE + 1):
                common.paper_row(number, paper)
            if results.total_pages > 1:
                with st.container(horizontal=True, vertical_alignment="center"):
                    if st.button("Previous", disabled=page <= 1):
                        st.session_state.result_page = page - 1
                        st.rerun()
                    st.caption(f"Page {page} of {results.total_pages}")
                    if st.button("Next", disabled=page >= results.total_pages):
                        st.session_state.result_page = page + 1
                        st.rerun()
            common.pubmed_attribution(results.attribution)

with answer_column, common.tile("peach", "evidence"):
    st.subheader("Evidence answer")
    st.write("Get a short answer written only from the stored open-access full text, "
             "with every claim cited and checked, or a clear “not enough evidence”.")
    st.page_link(common.ASK_PAGE, label="Ask the evidence →",
                 query_params={"q": query} if query else None)
    st.caption(common.PRIVACY_NOTICE)

common.footer()
