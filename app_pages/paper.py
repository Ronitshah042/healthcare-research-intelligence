"""Paper details, opened from a result: citation, links, open-access text, save."""

from __future__ import annotations

import streamlit as st

from app_pages import common
from hri import services

common.page_top(None)

st.page_link("app_pages/search.py", label="← Back to search")

identifier = st.query_params.get("id", "")
try:
    paper = services.get_paper(identifier)
except (services.InvalidRequestError, services.PaperNotFoundError):
    common.alert("not-found", "This paper isn’t in the stored collection. Open a paper from Search.")
    common.footer()
    st.stop()


def _open_access_passages(paper: services.StoredPaper) -> list[services.PassageMatch]:
    """This paper's stored open-access passages, if it has any.

    shortcut: found by searching passages for the title and matching the PMID or
    DOI; a services lookup by identifier would be exact, add one if this misses.
    """
    try:
        found = services.search_passages(paper.title, limit=services.MAX_PASSAGE_RESULTS)
    except services.InvalidRequestError:
        return []
    return [m for m in found.matches
            if (paper.pmid and m.document.pmid == paper.pmid)
            or (paper.doi and m.document.doi and m.document.doi.lower() == paper.doi)]


def _vancouver(paper: services.StoredPaper) -> str:
    lists = services.SessionSavedLists()
    lists.create("citation")
    lists.add("citation", common.identifier(paper))
    first = lists.export("citation", "citations").split("\n\n")[0]
    return first.removeprefix("1. ")


passages = _open_access_passages(paper)
document = passages[0].document if passages else None

hero, side = common.split_row("paper", [8, 4])
with hero, common.tile("hero", "paper"):
    badges = [":green-badge[Open-access full text]" if document else ":gray-badge[Citation only]"]
    if document and document.article_type:
        badges.append(f":gray-badge[{document.article_type.replace('-', ' ').capitalize()}]")
    if paper.publication_year:
        badges.append(f":gray-badge[{paper.publication_year}]")
    st.markdown(" ".join(badges))
    st.header(paper.title)
    authors = ", ".join(paper.authors[:6]) + (", et al." if len(paper.authors) > 6 else "")
    st.markdown(" · ".join(part for part in (
        authors or None, f"*{paper.journal}*" if paper.journal else None,
        f"PMID {paper.pmid}" if paper.pmid else None,
        document.pmcid if document else None) if part))
    with st.container(horizontal=True):
        if document:
            st.link_button("Read full article ↗", document.source_url, type="primary")
        if paper.pubmed_url:
            st.link_button("View on PubMed ↗", paper.pubmed_url)
        with st.popover("Save to list"):
            lists = common.saved_lists()
            with st.form("save", border=False):
                names = list(lists.names())
                chosen = st.selectbox("List", names + ["New list…"]) if names else "New list…"
                new_name = st.text_input("New list name", value="" if names else "Reading list")
                if st.form_submit_button("Save", type="primary"):
                    name = new_name.strip() if chosen == "New list…" else chosen
                    try:
                        if chosen == "New list…":
                            lists.create(name)
                        lists.add(name, common.identifier(paper))
                        st.success(f"Saved to “{name}”.")
                    except services.InvalidRequestError as error:
                        common.alert("save", str(error))
        with st.popover("Copy citation (Vancouver)"):
            st.code(_vancouver(paper), language=None, wrap_lines=True)
            st.caption("Use the copy icon. " + paper.attribution.source_name
                       if paper.attribution else "Use the copy icon.")

with side:
    left, right = st.columns(2)
    with left, common.tile("mint", "year"):
        st.metric("Year", paper.publication_year or "Unknown")
    with right, common.tile("mint", "licence"):
        st.metric("Licence", document.licence if document else "n/a",
                  help=None if document else "No open-access text is stored, only the citation.")
    with common.tile("white", "abstract"):
        st.markdown("**Abstract**")
        if paper.abstract_available and paper.pubmed_url:
            st.write("PubMed has an abstract. It isn’t stored here; read it on PubMed.")
        else:
            st.write("PubMed lists no abstract for this paper.")
        if document and document.notice_flags:
            common.alert("notice", "Notice on this article: "
                         + ", ".join(document.notice_flags) + ".")

text_column, ask_column = st.columns([7, 5], gap="medium")
with text_column, common.tile("white", "passages"):
    st.subheader("From the article’s own text")
    if not passages:
        st.write("No open-access full text is stored for this paper, so only its citation "
                 "is shown. Most journals’ text isn’t openly licensed.")
    else:
        shown = [m for m in sorted(passages, key=lambda m: m.chunk.position)
                 if m.chunk.section_name != "title"][:4]
        for match in shown:
            text = match.chunk.text
            with st.expander(f"{match.chunk.section_name.capitalize()}: {text[:90]}…"):
                st.write(text)
        st.caption(f"Open-access text reused under its licence. {document.attribution}")

with ask_column, common.tile("white", "ask"):
    st.subheader("Ask about this paper")
    with st.form("ask-paper", border=False):
        question = st.text_input("Your question")
        if st.form_submit_button("Ask", type="primary") and question.strip():
            st.switch_page(common.ASK_PAGE, query_params={"q": question.strip()})
    st.caption(common.PRIVACY_NOTICE)
    st.caption("The answer draws on all stored open-access articles, not only this one.")

common.pubmed_attribution(paper.attribution)
common.footer()
