"""Parse PMC JATS article XML into named sections of plain prose.

References, figures, tables, formulas, and supplementary material are
dropped. Only text is returned; nothing here contacts a network or stores
anything.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from xml.etree import ElementTree


SECTION_NAMES = (
    "title", "abstract", "introduction", "methods", "results", "discussion",
    "conclusion", "other",
)

# Elements whose whole content is dropped, wherever they occur.
_DROPPED_ELEMENTS = frozenset({
    "ref-list", "fig", "fig-group", "table-wrap", "table-wrap-group", "table",
    "disp-formula", "disp-formula-group", "supplementary-material", "media",
    "graphic", "inline-graphic", "alternatives", "fn-group", "fn",
    "object-id", "label",
})
# Citation markers point at the dropped references, so their text goes too.
_DROPPED_XREF_TYPES = frozenset({"bibr", "fn"})
# Top-level sections that are not evidence: matched against the heading.
_SKIPPED_HEADINGS = (
    "supporting information", "supplementary", "supplemental", "acknowledg",
    "funding", "conflict", "competing interest", "author contribution",
    "data availability", "availability of data", "ethics", "abbreviation",
    "declaration", "consent for publication",
)
_SKIPPED_SECTION_TYPES = frozenset({
    "supplementary-material", "data-availability", "COI-statement",
    "funding-information", "ethics-statement", "author-contributions",
})
# Checked in order, so "Results and discussion" counts as results.
_HEADING_KEYWORDS = (
    ("introduction", ("introduction", "background")),
    ("methods", ("method", "materials", "patients and", "study design",
                 "experimental procedure")),
    ("results", ("result", "findings")),
    ("discussion", ("discussion",)),
    ("conclusion", ("conclusion", "concluding")),
)
_SECTION_TYPE_NAMES = {
    "intro": "introduction",
    "introduction": "introduction",
    "background": "introduction",
    "methods": "methods",
    "materials": "methods",
    "materials|methods": "methods",
    "subjects": "methods",
    "results": "results",
    "discussion": "discussion",
    "conclusions": "conclusion",
    "conclusion": "conclusion",
}
_NOTICE_ARTICLE_TYPES = {
    "correction": "correction_notice",
    "retraction": "retraction_notice",
    "expression-of-concern": "expression_of_concern_notice",
}
_NOTICE_LINK_TYPES = {
    "correction-forward": "corrected",
    "corrected-article": "correction_notice",
    "partial-retraction": "partially_retracted",
}
_RETRACTION_LINK_TYPES = frozenset({"retraction-forward"})
_UNWANTED_ABSTRACT_TYPES = frozenset({
    "graphical", "teaser", "toc", "web-summary", "video", "author-highlights",
})
_CITATION_SEPARATORS = " \t\n,;-\u2013"
_EMPTY_BRACKETS = re.compile(r"\s*[\[(][\s,;\-–]*[\])]")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?)\]])")


class FullTextParseError(ValueError):
    """Raised when article XML cannot be parsed into any text."""


@dataclass(frozen=True)
class ArticleSection:
    """One section of an article: a canonical name and its paragraphs."""

    name: str
    # The heading as printed in the article, when it had one.
    heading: str | None
    paragraphs: tuple[str, ...]


@dataclass(frozen=True)
class ParsedArticle:
    """The prose of one article, in reading order, plus notice signals."""

    title: str
    sections: tuple[ArticleSection, ...]
    article_type: str | None
    # Corrections and concerns to warn about; see CONTENT_POLICY.md section 6.
    notice_flags: tuple[str, ...]
    # True when the XML links to a retraction of this article.
    links_to_retraction: bool


def parse_article(xml: bytes | str) -> ParsedArticle:
    """Parse JATS XML into sections; raise ``FullTextParseError`` if unusable."""
    text = xml.decode("utf-8", "replace") if isinstance(xml, bytes) else xml
    # PMC articles never define entities; refusing them avoids entity bombs.
    if "<!ENTITY" in text:
        raise FullTextParseError("Article XML must not define entities.")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise FullTextParseError("Article XML is malformed.") from error
    article = root if _local(root.tag) == "article" else root.find(".//article")
    if article is None:
        raise FullTextParseError("XML does not contain a JATS article.")

    _drop_unwanted(article)
    title_element = article.find("front/article-meta/title-group/article-title")
    title = _clean(_text_of(title_element)) if title_element is not None else ""
    sections: list[ArticleSection] = []
    if title:
        sections.append(ArticleSection("title", None, (title,)))
    abstract = _main_abstract(article)
    if abstract is not None:
        paragraphs = _paragraphs(abstract)
        if paragraphs:
            sections.append(ArticleSection("abstract", None, paragraphs))
    body = article.find("body")
    if body is not None:
        sections.extend(_body_sections(body))
    if not any(section.name != "title" for section in sections):
        raise FullTextParseError("Article XML contains no abstract or body text.")

    flags, links_to_retraction = _notices(article)
    return ParsedArticle(
        title=title,
        sections=tuple(sections),
        article_type=article.get("article-type"),
        notice_flags=flags,
        links_to_retraction=links_to_retraction,
    )


def _drop_unwanted(article: ElementTree.Element) -> None:
    for parent in list(article.iter()):
        for child in list(parent):
            tag = _local(child.tag)
            is_citation = (
                tag == "xref" and child.get("ref-type") in _DROPPED_XREF_TYPES
            )
            if is_citation and not (child.tail or "").strip(_CITATION_SEPARATORS):
                # Separators between consecutive citations ("1, 2") go too.
                child.tail = " "
            if tag in _DROPPED_ELEMENTS or is_citation:
                # Keep the text that followed the removed element.
                _remove_keeping_tail(parent, child)


def _remove_keeping_tail(
    parent: ElementTree.Element, child: ElementTree.Element
) -> None:
    tail = child.tail or ""
    if tail:
        children = list(parent)
        index = children.index(child)
        if index == 0:
            parent.text = (parent.text or "") + tail
        else:
            previous = children[index - 1]
            previous.tail = (previous.tail or "") + tail
    parent.remove(child)


def _main_abstract(article: ElementTree.Element) -> ElementTree.Element | None:
    abstracts = article.findall("front/article-meta/abstract")
    for abstract in abstracts:
        if abstract.get("abstract-type") not in _UNWANTED_ABSTRACT_TYPES:
            return abstract
    return None


def _body_sections(body: ElementTree.Element) -> list[ArticleSection]:
    sections: list[ArticleSection] = []
    loose = tuple(
        paragraph
        for child in body
        if _local(child.tag) == "p"
        for paragraph in [_clean(_text_of(child))]
        if paragraph
    )
    if loose:
        sections.append(ArticleSection("other", None, loose))
    for sec in body:
        if _local(sec.tag) != "sec":
            continue
        heading = _heading(sec)
        if _is_skipped(sec, heading):
            continue
        paragraphs = _paragraphs(sec)
        if paragraphs:
            sections.append(
                ArticleSection(_section_name(sec, heading), heading, paragraphs)
            )
    return sections


def _paragraphs(container: ElementTree.Element) -> tuple[str, ...]:
    """Return paragraphs in reading order, with subsection headings inline."""
    paragraphs: list[str] = []
    for child in container:
        tag = _local(child.tag)
        if tag == "p":
            text = _clean(_text_of(child))
            if text:
                paragraphs.append(text)
        elif tag == "sec":
            if _is_skipped(child, _heading(child)):
                continue
            heading = _heading(child)
            if heading:
                paragraphs.append(heading)
            paragraphs.extend(_paragraphs(child))
        elif tag in ("list", "boxed-text", "statement", "disp-quote"):
            for item in child.iter():
                if _local(item.tag) == "p":
                    text = _clean(_text_of(item))
                    if text:
                        paragraphs.append(text)
    return tuple(paragraphs)


def _heading(sec: ElementTree.Element) -> str | None:
    title = sec.find("title")
    return _clean(_text_of(title)) or None if title is not None else None


def _is_skipped(sec: ElementTree.Element, heading: str | None) -> bool:
    if sec.get("sec-type") in _SKIPPED_SECTION_TYPES:
        return True
    lowered = (heading or "").casefold()
    return any(phrase in lowered for phrase in _SKIPPED_HEADINGS)


def _section_name(sec: ElementTree.Element, heading: str | None) -> str:
    # Headings win over sec-type, which publishers sometimes set wrongly.
    lowered = re.sub(r"^[\d.\s]+", "", (heading or "").casefold())
    for name, keywords in _HEADING_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return name
    return _SECTION_TYPE_NAMES.get((sec.get("sec-type") or "").casefold(), "other")


def _notices(article: ElementTree.Element) -> tuple[tuple[str, ...], bool]:
    flags: set[str] = set()
    article_type = (article.get("article-type") or "").casefold()
    if article_type in _NOTICE_ARTICLE_TYPES:
        flags.add(_NOTICE_ARTICLE_TYPES[article_type])
    links_to_retraction = False
    for related in article.iter():
        if _local(related.tag) != "related-article":
            continue
        link_type = (related.get("related-article-type") or "").casefold()
        if link_type in _RETRACTION_LINK_TYPES:
            links_to_retraction = True
        elif link_type in _NOTICE_LINK_TYPES:
            flags.add(_NOTICE_LINK_TYPES[link_type])
        elif "concern" in link_type:
            flags.add("expression_of_concern")
    return tuple(sorted(flags)), links_to_retraction


def _text_of(element: ElementTree.Element) -> str:
    return "".join(element.itertext())


def _clean(text: str) -> str:
    text = " ".join(text.split())
    text = _EMPTY_BRACKETS.sub("", text)
    text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)
    return text.strip()


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""
