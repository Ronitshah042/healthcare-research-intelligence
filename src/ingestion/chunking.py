"""Split parsed article sections into overlapping passages for retrieval.

A token is approximated as a word or a punctuation mark, because the project
has no tokenizer (standard library only). Chunks never cross a section
boundary and break at sentence ends where possible.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from .fulltext import ArticleSection


TARGET_TOKENS = 400
MAX_TOKENS = 500
OVERLAP_TOKENS = 50

_TOKEN = re.compile(r"\w+|[^\w\s]")
# A sentence ends at . ! or ? followed by space and a capital, digit, or bracket.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"'])")


@dataclass(frozen=True)
class Chunk:
    """One passage of one section."""

    # Position of the section in the article, counting from 0.
    section_index: int
    section_name: str
    # Position of the chunk in the whole article, counting from 0.
    position: int
    text: str
    token_count: int


@dataclass(frozen=True)
class _Unit:
    text: str
    tokens: int
    starts_paragraph: bool


def count_tokens(text: str) -> int:
    """Return the approximate token count: words plus punctuation marks."""
    return len(_TOKEN.findall(text))


def chunk_sections(
    sections: Iterable[ArticleSection],
    *,
    target_tokens: int = TARGET_TOKENS,
    max_tokens: int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> tuple[Chunk, ...]:
    """Return chunks for every section, numbered across the whole article."""
    if not 0 <= overlap_tokens < target_tokens <= max_tokens:
        raise ValueError("Require 0 <= overlap_tokens < target_tokens <= max_tokens.")
    chunks: list[Chunk] = []
    for section_index, section in enumerate(sections):
        units = _units(section.paragraphs, max_tokens)
        for text, tokens in _pack(units, target_tokens, max_tokens, overlap_tokens):
            chunks.append(Chunk(
                section_index=section_index,
                section_name=section.name,
                position=len(chunks),
                text=text,
                token_count=tokens,
            ))
    return tuple(chunks)


def _units(paragraphs: Iterable[str], max_tokens: int) -> list[_Unit]:
    """Split paragraphs into sentences, and over-long sentences into pieces."""
    units: list[_Unit] = []
    for paragraph in paragraphs:
        first = True
        for sentence in _SENTENCE_BREAK.split(paragraph.strip()):
            for piece in _split_long(sentence, max_tokens):
                units.append(_Unit(piece, count_tokens(piece), first))
                first = False
    return [unit for unit in units if unit.tokens]


def _split_long(sentence: str, max_tokens: int) -> list[str]:
    if count_tokens(sentence) <= max_tokens:
        return [sentence]
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for word in sentence.split():
        word_tokens = count_tokens(word)
        if current and current_tokens + word_tokens > max_tokens:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(word)
        current_tokens += word_tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def _pack(
    units: list[_Unit], target: int, maximum: int, overlap: int
) -> list[tuple[str, int]]:
    chunks: list[tuple[str, int]] = []
    current: list[_Unit] = []
    has_new_text = False
    for unit in units:
        size = sum(item.tokens for item in current)
        if current and has_new_text and size + unit.tokens > target:
            chunks.append(_join(current))
            current = _overlap_tail(current, overlap)
            size = sum(item.tokens for item in current)
            has_new_text = False
        if size + unit.tokens > maximum:
            # The overlap must never push a chunk past the maximum.
            current = []
        current.append(unit)
        has_new_text = True
    if current and has_new_text:
        chunks.append(_join(current))
    return chunks


def _overlap_tail(units: list[_Unit], overlap: int) -> list[_Unit]:
    """Return the last whole sentences that fit within ``overlap`` tokens."""
    tail: list[_Unit] = []
    total = 0
    for unit in reversed(units):
        if total + unit.tokens > overlap:
            break
        tail.insert(0, unit)
        total += unit.tokens
    return tail


def _join(units: list[_Unit]) -> tuple[str, int]:
    text = ""
    for index, unit in enumerate(units):
        if index:
            text += "\n" if unit.starts_paragraph else " "
        text += unit.text
    return text, sum(unit.tokens for unit in units)
