"""Ask the evidence: a cited answer from stored open-access passages, or none."""

from __future__ import annotations

import streamlit as st

from app_pages import common
from hri import services

common.page_style()

fulltext = services.get_fulltext_stats()
quota = common.visitor_quota()

# A question carried over from Search or a paper page fills the box once; it is
# never sent until the visitor presses Ask (each question uses free AI quota).
incoming = st.query_params.get("q", "")
if incoming and incoming != st.session_state.get("question_from_link"):
    st.session_state.question_from_link = incoming
    st.session_state.question = incoming


def _suggest() -> None:
    topic = st.session_state.get("suggestion")
    if topic:
        st.session_state.question = f"What does the evidence say about {topic}?"


with common.tile("hero", "ask"):
    st.header("Ask the evidence")
    with st.form("ask", border=False):
        question = st.text_area("Your question", key="question", height=90,
                                max_chars=services.MAX_QUESTION_LENGTH)
        asked = st.form_submit_button("Ask", type="primary")
    st.caption(common.PRIVACY_NOTICE)
    quota_note = st.empty()  # filled after the question is handled, so the count is current

if asked:
    if not question.strip():
        common.alert("empty", "Type a question first.")
    elif not quota.try_use():
        common.alert("quota", "You have used this visit’s questions. Searching citations still works.")
    else:
        try:
            with st.spinner("Finding passages and writing a cited answer…"):
                st.session_state.answer = services.ask_question(question)
        except services.InvalidRequestError as error:
            common.alert("question", str(error))

quota_note.caption(f"{quota.remaining} of {services.DEFAULT_VISITOR_QUESTIONS} questions "
                   "left for this visit.")
answer = st.session_state.get("answer")
answer_column, side = st.columns([8, 4], gap="medium")
with answer_column, common.tile("white", "answer"):
    st.subheader("Answer")
    if answer is None:
        st.write("Ask a question above. The answer uses only passages from the stored "
                 "open-access articles, and every claim cites one.")
    else:
        st.markdown(f"**Question:** {answer.question}")
        if answer.abstained and answer.abstain_reason == "busy":
            common.alert("busy", "The AI service is busy right now, so no answer was "
                                 "written. Please try again later.")
        elif answer.abstained:
            common.alert("not-enough", f"Not enough evidence. {answer.text}")
            st.write("The stored articles don’t support an answer to this. Try rewording it, "
                     "or search the PubMed citations instead.")
            st.page_link("app_pages/search.py", label="Search citations →")
        else:
            st.caption("Every claim checked against its source")
            st.markdown(answer.text)
            for citation in answer.citations:
                with common.tile("white", f"source-{citation.number}"):
                    st.markdown(f"**[{citation.number}]** {citation.title}")
                    st.caption(" · ".join(part for part in (
                        citation.section, citation.licence, citation.pmcid) if part))
                    with st.container(horizontal=True):
                        st.link_button("Open the article ↗", citation.link)
                        if citation.pmid:
                            st.page_link(common.PAPER_PAGE, label="Paper details",
                                         query_params={"id": citation.pmid})
                    if citation.attribution:
                        st.caption(str(citation.attribution))
        if answer.notice:
            st.caption(answer.notice)
        st.caption(answer.disclaimer)
    st.markdown(f"**{common.NOT_ADVICE}**")

with side:
    with common.tile("mint", "how"):
        st.subheader("How this answer was made")
        st.markdown(
            f"1. Found the most relevant passages in {fulltext.documents} open-access articles\n"
            "2. Wrote an answer using only those passages\n"
            "3. Removed any citation that didn’t match a passage")
    with common.tile("white", "missing"):
        st.markdown("**When evidence is missing**")
        st.write("If the stored articles don’t cover a question, or the passages found are "
                 "only loosely related, the tool says “not enough evidence” instead of "
                 "guessing.")

topics = [topic["topic"] for topic in common.MANIFEST.get("topics", []) if topic.get("topic")]
if topics:
    with common.tile("mint", "suggestions"):
        st.pills("Try asking about", topics, key="suggestion", on_change=_suggest)

common.footer()
