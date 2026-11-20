"""Tests for splitting article sections into passages."""

import re
import unittest
from pathlib import Path

from ingestion.chunking import (
    MAX_TOKENS,
    OVERLAP_TOKENS,
    chunk_sections,
    count_tokens,
)
from ingestion.fulltext import ArticleSection, parse_article

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pmc"


def sentence(number: int, words: int = 9) -> str:
    """A sentence of ``words`` words plus a full stop: ``words + 1`` tokens."""
    return "S" + " ".join(f"w{number}" for _ in range(words)) + "."


class TokenTests(unittest.TestCase):
    def test_counts_words_and_punctuation(self) -> None:
        self.assertEqual(count_tokens("Risk rose (p < 0.05)."), 10)
        self.assertEqual(count_tokens(""), 0)
        self.assertEqual(count_tokens("Ärnlöv's cohort"), 4)


class ChunkingTests(unittest.TestCase):
    def test_short_sections_become_one_chunk_each(self) -> None:
        chunks = chunk_sections([
            ArticleSection("title", None, ("A title",)),
            ArticleSection("abstract", None, ("One. Two.", "Three.")),
        ])

        self.assertEqual(
            [(c.section_index, c.section_name, c.position, c.text) for c in chunks],
            [(0, "title", 0, "A title"), (1, "abstract", 1, "One. Two.\nThree.")],
        )

    def test_never_crosses_a_section_boundary(self) -> None:
        sections = [
            ArticleSection("methods", "Methods", (sentence(1),)),
            ArticleSection("results", "Results", (sentence(2),)),
        ]

        chunks = chunk_sections(sections, target_tokens=100, overlap_tokens=5)

        self.assertEqual([c.section_name for c in chunks], ["methods", "results"])
        self.assertNotIn("w2", chunks[0].text)

    def test_long_sections_split_at_sentences_with_overlap(self) -> None:
        paragraph = " ".join(sentence(n) for n in range(1, 31))  # 30 x 10 tokens
        chunks = chunk_sections(
            [ArticleSection("results", None, (paragraph,))],
            target_tokens=100, max_tokens=120, overlap_tokens=20,
        )

        self.assertGreater(len(chunks), 3)
        for chunk in chunks:
            self.assertLessEqual(chunk.token_count, 120)
            self.assertTrue(chunk.text.endswith("."))
            self.assertEqual(chunk.token_count, count_tokens(chunk.text))
        numbers = [re.findall(r"Sw(\d+)", chunk.text) for chunk in chunks]
        self.assertEqual(numbers[0], [str(n) for n in range(1, 11)])
        for previous, current in zip(numbers, numbers[1:]):
            # 20 overlap tokens = the previous chunk's last two sentences.
            self.assertEqual(current[:2], previous[-2:])
        joined = " ".join(chunk.text for chunk in chunks)
        for n in range(1, 31):
            self.assertIn(f"w{n}", joined)
        self.assertEqual([c.position for c in chunks], list(range(len(chunks))))

    def test_overlap_never_exceeds_the_limit_and_always_adds_new_text(self) -> None:
        paragraph = " ".join(sentence(n) for n in range(1, 21))
        chunks = chunk_sections(
            [ArticleSection("results", None, (paragraph,))],
            target_tokens=30, max_tokens=40, overlap_tokens=10,
        )

        seen: set[str] = set()
        for chunk in chunks:
            words = set(chunk.text.replace(".", "").split())
            self.assertTrue(words - seen, "every chunk must add new sentences")
            seen |= words
            self.assertLessEqual(chunk.token_count, 40)

    def test_splits_a_sentence_longer_than_the_maximum(self) -> None:
        long_sentence = " ".join(f"word{n}" for n in range(250)) + "."
        chunks = chunk_sections(
            [ArticleSection("methods", None, (long_sentence,))],
            target_tokens=80, max_tokens=100, overlap_tokens=0,
        )

        self.assertTrue(all(chunk.token_count <= 100 for chunk in chunks))
        self.assertEqual(
            " ".join(chunk.text for chunk in chunks).split(), long_sentence.split()
        )

    def test_rejects_inconsistent_sizes(self) -> None:
        for sizes in (
            {"target_tokens": 100, "max_tokens": 50},
            {"target_tokens": 100, "overlap_tokens": 100},
            {"overlap_tokens": -1},
        ):
            with self.subTest(sizes=sizes):
                with self.assertRaises(ValueError):
                    chunk_sections([], **sizes)

    def test_real_article_chunks_stay_within_the_defaults(self) -> None:
        article = parse_article((FIXTURES / "PMC10009402.1.xml").read_bytes())

        chunks = chunk_sections(article.sections)

        self.assertEqual(chunks[0].section_name, "title")
        self.assertEqual(
            sorted({c.section_name for c in chunks}),
            sorted({s.name for s in article.sections}),
        )
        self.assertTrue(all(c.token_count <= MAX_TOKENS for c in chunks))
        self.assertTrue(all(c.text.strip() for c in chunks))
        self.assertLess(OVERLAP_TOKENS, MAX_TOKENS)


if __name__ == "__main__":
    unittest.main()
