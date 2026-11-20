"""Settings for the deployed app: Streamlit secrets or environment variables.

Every service reads its settings from environment variables. On Streamlit
Community Cloud the settings are entered as secrets instead, so the app
copies the known ones into the environment once at start-up. A variable that
is already set wins over a secret.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping

__all__ = ["SETTING_NAMES", "APP_DEFAULTS", "apply_settings", "get_setting"]

# Never logged or shown: only their names are reported.
SETTING_NAMES = (
    "GEMINI_API_KEY", "GROQ_API_KEY",
    "PUBMED_NCBI_TOOL", "PUBMED_NCBI_EMAIL", "PUBMED_NCBI_API_KEY",
    "HRI_BUNDLE_URL", "HRI_BUNDLE_DIR", "HRI_MODEL_CACHE",
    "HRI_LLM_DAILY_REQUEST_CAP", "HRI_GEMINI_MODEL", "HRI_GROQ_MODEL",
)
# The deployed app reads a bundle it must never change, and protects the
# owner's free AI quota more tightly than the local default of 100.
APP_DEFAULTS = {"HRI_DB_READ_ONLY": "1", "HRI_LLM_DAILY_REQUEST_CAP": "50"}


def apply_settings(
    secrets: Mapping[str, object] | None,
    environment: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Copy known secrets into the environment, then apply the app defaults.

    Returns the names that were set (never the values).
    """
    env = os.environ if environment is None else environment
    applied = []
    for name in SETTING_NAMES:
        value = (secrets or {}).get(name)
        if value is not None and str(value).strip() and not env.get(name, "").strip():
            env[name] = str(value).strip()
            applied.append(name)
    for name, value in APP_DEFAULTS.items():
        if not env.get(name, "").strip():
            env[name] = value
            applied.append(name)
    return tuple(applied)


def get_setting(
    name: str,
    secrets: Mapping[str, object] | None = None,
    default: str = "",
    environment: Mapping[str, str] | None = None,
) -> str:
    """An environment variable, else the secret, else ``default``."""
    env = os.environ if environment is None else environment
    value = env.get(name, "").strip()
    if value:
        return value
    secret = (secrets or {}).get(name)
    return str(secret).strip() if secret is not None and str(secret).strip() else default
