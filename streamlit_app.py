"""Research Intelligence: the Streamlit app (the owner's "Bento Vitals" design).

Checks the deployment once (settings, data bundle, start-up self-check,
embedding model), then runs one of four pages in app_pages/. Pages call only
hri.services; see docs/SERVICE_API.md and docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import streamlit as st  # noqa: E402

from app_pages import common  # noqa: E402
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


st.set_page_config(page_title="Research Intelligence", layout="wide",
                   page_icon=str(ROOT / "app_pages" / "logo-icon.svg"))

try:
    bundle = prepare_bundle()
except Exception as error:  # show the problem instead of a stack trace
    common.alert("bundle", f"Could not get the data bundle: {error}")
    st.stop()
os.environ["HRI_DB_PATH"] = bundle
check = services.check_deployment(bundle, embedder_id=embeddings.MODEL_ID)
if not check.ok:
    for number, problem in enumerate(check.problems):
        common.alert(f"check-{number}", problem)
    st.stop()
common.MANIFEST = check.manifest or {}

model = prepare_embedder()
if not model.available:
    st.caption("Meaning-based search is unavailable, so search uses keywords only: "
               + model.message)

st.navigation([
    st.Page("app_pages/search.py", title="Search", default=True),
    st.Page("app_pages/ask.py", title="Ask"),
    st.Page("app_pages/saved.py", title="Saved"),
    st.Page("app_pages/paper.py", title="Paper details", visibility="hidden"),
], position="hidden").run()  # each page draws its own header (common.page_top)
