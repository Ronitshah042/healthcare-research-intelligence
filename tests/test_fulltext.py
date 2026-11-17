"""Tests for parsing PMC JATS XML into sections, using recorded fixtures."""

import unittest
from pathlib import Path

from ingestion.fulltext import (
    SECTION_NAMES,
    FullTextParseError,
    parse_article,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pmc"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def article(body: str = "", *, front: str = "", article_type: str = "research-article",
            back: str = "") -> str:
    return (
        f'<article article-type="{article_type}" '
        'xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>'
        "<title-group><article-title>A <italic>study</italic></article-title>"
        f"</title-group>{front}</article-meta></front><body>{body}</body>"
        f"<back>{back}</back></article>"
    )


class RecordedArticleTests(unittest.TestCase):
    def test_parses_a_cc0_article_into_canonical_sections(self) -> None:
        parsed = parse_article(fixture("PMC12826829.1.xml"))

        self.assertEqual(
            [section.name for section in parsed.sections],
            ["title", "abstract", "introduction", "methods", "results",
             "discussion", "conclusion"],
        )
        self.assertTrue(parsed.title.startswith("Prevalence, correlates"))
        self.assertEqual(parsed.article_type, "research-article")
        self.assertEqual((parsed.notice_flags, parsed.links_to_retraction), ((), False))

    def test_reads_authors_and_licence_url(self) -> None:
        cc0 = parse_article(fixture("PMC12826829.1.xml"))
        cc_by = parse_article(fixture("PMC10009402.1.xml"))

        self.assertEqual(len(cc0.authors), 3)
        self.assertEqual(cc_by.authors[0], "Magnus Helgesson")
        self.assertEqual(len(cc_by.authors), 8)
        self.assertEqual(
            cc0.licence_url, "https://creativecommons.org/publicdomain/zero/1.0/"
        )
        self.assertEqual(cc_by.licence_url, "https://creativecommons.org/licenses/by/4.0/")

    def test_heading_wins_over_a_wrong_section_type(self) -> None:
        # The publisher tagged "4. Discussion" as sec-type="conclusions".
        parsed = parse_article(fixture("PMC12826829.1.xml"))
        discussion = [s for s in parsed.sections if s.heading == "4. Discussion"]

        self.assertEqual([s.name for s in discussion], ["discussion"])

    def test_drops_references_tables_supplements_and_citation_markers(self) -> None:
        raw = fixture("PMC10009402.1.xml").decode()
        parsed = parse_article(raw)
        text = " ".join(p for s in parsed.sections for p in s.paragraphs)

        self.assertIn("<ref-list", raw)
        self.assertIn('ref-type="bibr"', raw)
        self.assertIn("<table-wrap", raw)
        for absent in ("Supporting information", "<", "[]", "()", " ,", " ."):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, text)
        self.assertNotIn(
            "Supporting information",
            " ".join(p for s in parse_article(fixture("PMC12826829.1.xml")).sections
                     for p in s.paragraphs),
        )

    def test_keeps_the_structured_abstract_and_subsection_headings(self) -> None:
        parsed = parse_article(fixture("PMC10009402.1.xml"))
        abstract = next(s for s in parsed.sections if s.name == "abstract")
        methods = next(s for s in parsed.sections if s.name == "methods")

        self.assertEqual(abstract.paragraphs[0], "Background")
        self.assertEqual(methods.paragraphs[0], "Registers")
        self.assertTrue(all(name in SECTION_NAMES for name in
                            (s.name for s in parsed.sections)))


class EdgeCaseTests(unittest.TestCase):
    def test_classifies_headings_and_section_types(self) -> None:
        body = (
            '<sec><title>Background</title><p>a</p></sec>'
            '<sec sec-type="methods"><title>Study population</title><p>b</p></sec>'
            '<sec><title>Results and discussion</title><p>c</p></sec>'
            '<sec><title>Case presentation</title><p>d</p></sec>'
            '<sec><title>Limitations</title><p>e</p></sec>'
            '<sec><title>Funding</title><p>f</p></sec>'
            '<sec sec-type="data-availability"><title>Data</title><p>g</p></sec>'
        )
        parsed = parse_article(article(body))

        self.assertEqual(
            [(s.name, s.heading) for s in parsed.sections[1:]],
            [("introduction", "Background"), ("methods", "Study population"),
             ("results", "Results and discussion"), ("other", "Case presentation"),
             ("other", "Limitations")],
        )

    def test_body_paragraphs_outside_sections_become_other(self) -> None:
        parsed = parse_article(article("<p>Loose text.</p>"))

        self.assertEqual(parsed.sections[-1].name, "other")
        self.assertEqual(parsed.sections[-1].paragraphs, ("Loose text.",))

    def test_cleans_markup_citations_and_whitespace(self) -> None:
        body = (
            "<sec><title>Introduction</title><p>Risk rose <xref ref-type=\"bibr\" "
            "rid=\"r1\">1</xref>, <xref ref-type=\"bibr\" rid=\"r2\">2</xref> "
            "[<xref ref-type=\"bibr\" rid=\"r3\">3</xref>] in <italic>older</italic>\n"
            "  adults (<xref ref-type=\"table\" rid=\"t1\">Table 1</xref>)."
            "<fig><caption><p>Figure text</p></caption></fig>"
            "<disp-formula>x=1</disp-formula> End.</p></sec>"
        )
        paragraph = parse_article(article(body)).sections[-1].paragraphs[0]

        self.assertEqual(paragraph, "Risk rose in older adults (Table 1). End.")

    def test_keeps_punctuation_that_follows_the_last_citation(self) -> None:
        body = (
            "<sec><title>Introduction</title><p>Seen before"
            '<xref ref-type="bibr" rid="r1">1</xref>,'
            '<xref ref-type="bibr" rid="r2">2</xref>. Also later'
            '<xref ref-type="bibr" rid="r3">3</xref>, and again.</p></sec>'
        )
        paragraph = parse_article(article(body)).sections[-1].paragraphs[0]

        self.assertEqual(paragraph, "Seen before. Also later, and again.")

    def test_prefers_the_normal_abstract_over_graphical_ones(self) -> None:
        front = (
            '<abstract abstract-type="graphical"><p>Picture</p></abstract>'
            "<abstract><p>Main abstract.</p></abstract>"
        )
        parsed = parse_article(article(front=front))

        self.assertEqual(parsed.sections[1].paragraphs, ("Main abstract.",))

    def test_reads_notice_flags_and_retraction_links(self) -> None:
        def related(kind: str) -> str:
            return f'<p><related-article related-article-type="{kind}"/>x</p>'

        cases = {
            "correction-forward": (("corrected",), False),
            "partial-retraction": (("partially_retracted",), False),
            "expression-of-concern": (("expression_of_concern",), False),
            "object-of-concern": (("expression_of_concern",), False),
            "retraction-forward": ((), True),
            "companion": ((), False),
        }
        for kind, (flags, retracted) in cases.items():
            with self.subTest(kind=kind):
                parsed = parse_article(article(related(kind)))
                self.assertEqual((parsed.notice_flags, parsed.links_to_retraction),
                                 (flags, retracted))

        for article_type, flag in (
            ("correction", "correction_notice"),
            ("retraction", "retraction_notice"),
            ("expression-of-concern", "expression_of_concern_notice"),
        ):
            with self.subTest(article_type=article_type):
                parsed = parse_article(article("<p>x</p>", article_type=article_type))
                self.assertEqual(parsed.notice_flags, (flag,))

    def test_reads_group_authors_and_skips_editors(self) -> None:
        front = (
            '<contrib-group><contrib contrib-type="author"><name><surname>Smith'
            "</surname><given-names>Jo</given-names></name></contrib>"
            '<contrib contrib-type="author"><collab>WHO Study Group</collab></contrib>'
            '<contrib contrib-type="editor"><name><surname>Ed</surname></name></contrib>'
            "</contrib-group>"
            '<permissions><license xlink:href="https://example.org/l"><p>x</p>'
            "</license></permissions><abstract><p>A.</p></abstract>"
        )
        parsed = parse_article(article(front=front))

        self.assertEqual(parsed.authors, ("Jo Smith", "WHO Study Group"))
        self.assertEqual(parsed.licence_url, "https://example.org/l")
        self.assertIsNone(parse_article(article("<p>x</p>")).licence_url)

    def test_rejects_unusable_xml(self) -> None:
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xxxxxxxx">]>'
            "<article><body><p>&x;</p></body></article>"
        )
        for xml in (
            "<article><body>", "<not-an-article/>", bomb,
            article(""), b"\xff\xfe not xml",
        ):
            with self.subTest(xml=xml[:30]):
                with self.assertRaises(FullTextParseError):
                    parse_article(xml)


if __name__ == "__main__":
    unittest.main()
