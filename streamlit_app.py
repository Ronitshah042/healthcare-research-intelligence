"""PLACEHOLDER page for Streamlit Community Cloud: the real UI is being designed.

It only proves the deployment works: settings, data bundle, start-up
self-check, embedding model, and two service calls (get_stats, search_papers).
All application logic lives in hri.services; see docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

from hri import app_config, services  # noqa: E402
from retrieval import embeddings  # noqa: E402


def _secrets() -> dict:
    try:
        return dict(st.secrets)
    except Exception:  # no secrets.toml when run locally
        return {}


app_config.apply_settings(_secrets())


@st.cache_resource(show_spinner="Fetching the data bundle...")
def prepare_bundle() -> str:
    """Download (once per app process) and verify the bundle, or use a local one."""
    directory = services.bundle_directory()
    url = os.environ.get("HRI_BUNDLE_URL", "").strip()
    path = services.fetch_bundle(url, directory=directory) if url else (
        directory / services.BUNDLE_NAME)
    return str(path)


@st.cache_resource(show_spinner="Loading the embedding model...")
def prepare_embedder():
    """Download (once) and load the embedding model; keyword search works without it."""
    return services.prepare_embedder()


st.set_page_config(page_title="Healthcare Research Intelligence (placeholder)")
st.title("Healthcare Research Intelligence")
st.warning("Placeholder page, real UI coming. This page only checks that the "
           "deployment works. Research aid only, not medical advice.")

try:
    bundle = prepare_bundle()
except Exception as error:  # show the problem instead of a stack trace
    st.error(f"Could not get the data bundle: {error}")
    st.stop()
os.environ["HRI_DB_PATH"] = bundle
check = services.check_deployment(bundle, embedder_id=embeddings.MODEL_ID)
if not check.ok:
    for problem in check.problems:
        st.error(problem)
    st.stop()

model = prepare_embedder()
stats = services.get_stats()
manifest = check.manifest or {}
st.write(f"Data bundle built {manifest.get('built_at', 'unknown')}: "
         f"{stats.total_papers} citations, "
         f"{manifest.get('counts', {}).get('articles', 0)} open-access articles.")
st.write("Meaning-based search: " + ("ready" if model.available else model.message))

query = st.text_input("Search stored citations (placeholder)")
if query.strip():
    try:
        results = services.search_papers(query)
    except services.InvalidRequestError as error:
        st.error(str(error))
    else:
        st.write(f"{results.total_count} matching citations")
        for paper in results.papers:
            st.write(f"- {paper.title} ({paper.publication_year}) {paper.pubmed_url or ''}")
        if results.attribution:
            st.caption(f"{results.attribution.source_name} "
                       f"{results.attribution.independent_service_notice}")
