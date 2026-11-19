"""Text embeddings for meaning-based passage search.

``fastembed`` is an optional dependency (``pip install -e .[embeddings]``,
Python 3.10 to 3.13). Without it, or without the downloaded model, callers
fall back to keyword search. The model, its revision, and its file hash are
pinned here and in docs/DECISIONS.md and docs/CONTENT_POLICY.md.

Only ``prepare`` downloads, from Hugging Face; loading for search is offline.
"""

from __future__ import annotations

from array import array
import hashlib
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Iterable, Protocol


MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_REPOSITORY = "Qdrant/bge-small-en-v1.5-onnx-Q"
MODEL_REVISION = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
MODEL_FILE = "model_optimized.onnx"
MODEL_FILE_SIZE = 66_465_124
MODEL_FILE_SHA256 = "51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431"
MODEL_FILES = (
    MODEL_FILE, "config.json", "special_tokens_map.json", "tokenizer.json",
    "tokenizer_config.json",
)
MODEL_ID = f"{MODEL_NAME}@{MODEL_REVISION[:7]}"
MODEL_DIMENSIONS = 384
# Inputs longer than this many model tokens are cut off when embedded.
MODEL_MAX_TOKENS = 512
# BGE v1.5 expects this instruction before short queries for passage retrieval.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
DEFAULT_MODEL_CACHE = Path("data") / "models"


class EmbedderUnavailableError(RuntimeError):
    """Raised when meaning-based search cannot run; keyword search still can."""


class Embedder(Protocol):
    """Turns questions and passages into unit-length vectors."""

    model_id: str

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def count_tokens(self, text: str) -> int: ...


def model_cache_dir(environment: dict[str, str] | None = None) -> Path:
    """Return ``HRI_MODEL_CACHE`` if set, else ``data/models``."""
    value = (environment if environment is not None else os.environ).get(
        "HRI_MODEL_CACHE", ""
    ).strip()
    return Path(value) if value else DEFAULT_MODEL_CACHE


def model_dir(cache_dir: Path) -> Path:
    return cache_dir / MODEL_REPOSITORY.replace("/", "--") / MODEL_REVISION


def prepare(cache_dir: Path) -> Path:
    """Download the pinned model files once, verify them, and return their folder.

    Does nothing if the verified files are already cached. A file that fails
    the size or SHA-256 check is deleted and the error raised.
    """
    target = model_dir(cache_dir)
    marker = target / ".verified"
    if marker.exists() and all((target / name).exists() for name in MODEL_FILES):
        return target
    _require_fastembed()
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=MODEL_REPOSITORY,
            revision=MODEL_REVISION,
            allow_patterns=list(MODEL_FILES),
            local_dir=str(target),
        )
    except Exception as error:  # any download failure means: keyword only
        raise EmbedderUnavailableError(
            "Could not download the embedding model from Hugging Face "
            f"({type(error).__name__})."
        ) from None
    model_path = target / MODEL_FILE
    if (
        not model_path.exists()
        or model_path.stat().st_size != MODEL_FILE_SIZE
        or _sha256(model_path) != MODEL_FILE_SHA256
    ):
        shutil.rmtree(target, ignore_errors=True)
        raise EmbedderUnavailableError(
            "The downloaded embedding model failed its size or SHA-256 check "
            "and was deleted."
        )
    marker.write_text(MODEL_FILE_SHA256 + "\n")
    return target


class FastEmbedder:
    """The pinned BGE model through fastembed, loaded from the local cache only."""

    model_id = MODEL_ID

    def __init__(self, cache_dir: Path) -> None:
        _require_fastembed()
        target = model_dir(cache_dir)
        if not (target / ".verified").exists():
            raise EmbedderUnavailableError(
                "The embedding model is not downloaded yet; run "
                "`hri embed-passages` once."
            )
        from fastembed import TextEmbedding
        from tokenizers import Tokenizer

        try:
            self._model = TextEmbedding(
                model_name=MODEL_NAME,
                cache_dir=str(target),
                specific_model_path=str(target),
                local_files_only=True,
            )
        except Exception as error:
            raise EmbedderUnavailableError(
                f"Could not load the embedding model ({type(error).__name__})."
            ) from None
        self._tokenizer = Tokenizer.from_file(str(target / "tokenizer.json"))
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [unit(vector.tolist()) for vector in self._model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        vectors = self._model.query_embed([BGE_QUERY_PREFIX + text])
        return unit(next(iter(vectors)).tolist())

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text).ids)


def unit(vector: Iterable[float]) -> list[float]:
    """Return ``vector`` scaled to length 1 (unchanged if all zeros)."""
    values = [float(value) for value in vector]
    length = math.sqrt(sum(value * value for value in values))
    return [value / length for value in values] if length else values


def to_blob(vector: list[float]) -> bytes:
    values = array("f", vector)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def from_blob(blob: bytes) -> array:
    values = array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def _require_fastembed() -> None:
    # Read by huggingface_hub at import: no telemetry or agent-registry calls,
    # and plain HTTPS downloads (no Xet storage), so the hosts are the
    # documented ones. Set before fastembed imports huggingface_hub.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        import fastembed  # noqa: F401
    except ImportError:
        raise EmbedderUnavailableError(
            "Meaning-based search needs the optional embeddings install: "
            "pip install -e '.[embeddings]' on Python 3.10 to 3.13."
        ) from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
