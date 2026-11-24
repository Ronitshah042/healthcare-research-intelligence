"""Saved lists: this visitor's lists, kept for the session, with exports."""

from __future__ import annotations

import streamlit as st

from app_pages import common
from hri import services

common.page_style()

lists = common.saved_lists()
names = list(lists.names())
if st.session_state.get("selected_list") not in names:
    st.session_state.selected_list = names[0] if names else None
selected = st.session_state.selected_list

menu, content = st.columns([4, 8], gap="medium")
with menu, common.tile("hero", "lists"):
    st.header("Your lists")
    for name in names:
        label = f"{name} · {len(lists.papers(name))}"
        if st.button(label, key=f"list-{name}", width="stretch",
                     type="primary" if name == selected else "secondary"):
            st.session_state.selected_list = name
            st.rerun()
    with st.form("new-list", border=False, clear_on_submit=True):
        new_name = st.text_input("New list name")
        if st.form_submit_button("+ New list", width="stretch") and new_name.strip():
            try:
                lists.create(new_name)
                st.session_state.selected_list = new_name.strip()
                st.rerun()
            except services.InvalidRequestError as error:
                st.error(str(error))
    st.caption("Lists are kept for this visit only. Export before you leave to keep them.")

with content, common.tile("white", "list"):
    if selected is None:
        st.subheader("No lists yet")
        st.write("Create a list here, or open a paper from Search and choose “Save to list”.")
    else:
        papers = lists.papers(selected)
        st.subheader(selected)
        st.caption(f"{len(papers)} paper{'s' if len(papers) != 1 else ''}")
        with st.container(horizontal=True):
            with st.popover("Rename"):
                with st.form("rename", border=False):
                    renamed = st.text_input("New name", value=selected)
                    if st.form_submit_button("Rename", type="primary"):
                        try:
                            lists.rename(selected, renamed)
                            st.session_state.selected_list = renamed.strip()
                            st.rerun()
                        except services.InvalidRequestError as error:
                            st.error(str(error))
            with st.popover("Copy citations (Vancouver)", disabled=not papers):
                st.code(lists.export(selected, "citations"), language=None, wrap_lines=True)
            st.download_button("Export CSV", lists.export(selected, "csv"),
                               file_name=f"{selected}.csv", mime="text/csv",
                               type="primary", disabled=not papers)
            if st.button("Delete list"):
                lists.delete(selected)
                st.rerun()
        if not papers:
            st.write("This list is empty. Open a paper from Search and choose “Save to list”.")
        for number, paper in enumerate(papers, start=1):
            with st.container(horizontal=True, vertical_alignment="top"):
                with st.container():
                    common.paper_row(number, paper)
                if st.button("Remove", key=f"remove-{number}",
                             help=f"Remove “{paper.title}” from this list"):
                    lists.remove(selected, common.identifier(paper))
                    st.rerun()
        st.caption("Exports keep PubMed attribution: the citation text ends with it, and the "
                   "CSV’s source column names PubMed.")
        common.pubmed_attribution(papers[0].attribution if papers else None)

common.footer()
