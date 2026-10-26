"""Tests for the PubMed E-utilities ingestion client."""

from __future__ import annotations

from io import BytesIO
import socket
from threading import Event, Thread
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from ingestion.metadata import PaperMetadata
from ingestion.pubmed import (
    PubMedClient,
    PubMedHttpError,
    PubMedRequestValidationError,
    PubMedResponseError,
    PubMedTimeoutError,
    PubMedTransportError,
    SearchMode,
)
from ingestion.pubmed_config import PubMedClientConfig, PubMedConfigurationError


class FakeTransport:
    """Return queued responses and retain request details without using a network."""

    def __init__(self, responses: list[bytes | Exception]) -> None:
        self._responses = responses.copy()
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout_seconds: float) -> bytes:
        self.calls.append((url, timeout_seconds))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _esearch_response(count: int, *, webenv: str = "test-env", query_key: str = "1") -> bytes:
    history = "" if count == 0 else f"<WebEnv>{webenv}</WebEnv><QueryKey>{query_key}</QueryKey>"
    return f"<eSearchResult><Count>{count}</Count>{history}</eSearchResult>".encode()


def _article_xml(
    *,
    pmid: str = "12345",
    title: str = "Example paper",
    year: str | None = "2024",
    medline_date: str | None = None,
    abstract: str | None = None,
    article_year: str | None = None,
    history_year: str | None = None,
    include_author: bool = True,
) -> str:
    if year is None:
        publication_date = (
            f"<MedlineDate>{medline_date}</MedlineDate>" if medline_date else ""
        )
    else:
        publication_date = f"<Year>{year}</Year>"

    authors = (
        "<AuthorList><Author><ForeName>Ada</ForeName>"
        "<LastName>Lovelace</LastName></Author></AuthorList>"
        if include_author
        else ""
    )
    abstract_xml = f"<Abstract><AbstractText>{abstract}</AbstractText></Abstract>" if abstract else ""
    article_date = f"<ArticleDate><Year>{article_year}</Year></ArticleDate>" if article_year else ""
    history = (
        f"<PubmedData><History><PubMedPubDate><Year>{history_year}</Year>"
        "</PubMedPubDate></History><ArticleIdList>"
        "<ArticleId IdType=\"doi\">10.1234/example</ArticleId>"
        "</ArticleIdList></PubmedData>"
        if history_year
        else "<PubmedData><ArticleIdList><ArticleId IdType=\"doi\">"
        "10.1234/example</ArticleId></ArticleIdList></PubmedData>"
    )
    return (
        "<PubmedArticle><MedlineCitation>"
        f"<PMID>{pmid}</PMID><Article><Journal><Title>Example Journal</Title>"
        f"<JournalIssue><PubDate>{publication_date}</PubDate></JournalIssue>"
        f"</Journal>{article_date}<ArticleTitle>{title}</ArticleTitle>{authors}{abstract_xml}"
        f"</Article></MedlineCitation>{history}</PubmedArticle>"
    )


def _efetch_response(*articles: str) -> bytes:
    return f"<PubmedArticleSet>{''.join(articles)}</PubmedArticleSet>".encode()


def _parameters(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


class PubMedClientTests(unittest.TestCase):
    """Verify request construction, responses, and local failure handling."""

    @staticmethod
    def _config(**overrides: object) -> PubMedClientConfig:
        values: dict[str, object] = {
            "tool": "healthcare_research_intelligence",
            "email": "developer@example.org",
            "timeout_seconds": 4,
        }
        values.update(overrides)
        return PubMedClientConfig(**values)  # type: ignore[arg-type]

    @staticmethod
    def _client(transport: FakeTransport, **config_overrides: object) -> PubMedClient:
        return PubMedClient(
            PubMedClientTests._config(**config_overrides),
            transport=transport,
            clock=lambda: 0.0,
            sleeper=lambda _: None,
        )

    def test_search_modes_build_documented_field_queries(self) -> None:
        searches = [
            ("title", "[ti]", lambda client: client.search_title("heart failure")),
            (
                "title abstract",
                "[tiab]",
                lambda client: client.search_title_abstract("heart failure"),
            ),
            ("author", "[au]", lambda client: client.search_author("Lovelace A")),
            ("doi", "[aid]", lambda client: client.lookup_doi("10.1234/example")),
            ("pmid", "[pmid]", lambda client: client.lookup_pmid("12345")),
        ]

        for name, field_tag, search in searches:
            with self.subTest(search=name):
                transport = FakeTransport(
                    [_esearch_response(1), _efetch_response(_article_xml())]
                )
                result = search(self._client(transport))

                self.assertEqual(result.total_count, 1)
                self.assertIn(field_tag, _parameters(transport.calls[0][0])["term"][0])
                self.assertEqual(len(transport.calls), 2)

    def test_title_search_falls_back_to_title_abstract_with_same_year_filter(self) -> None:
        transport = FakeTransport(
            [
                _esearch_response(0),
                _esearch_response(1),
                _efetch_response(_article_xml()),
            ]
        )

        result = self._client(transport).search_title(
            "heart failure", publication_year=2020, publication_year_end=2022
        )

        self.assertTrue(result.fallback_used)
        self.assertEqual(result.total_count, 1)
        self.assertIn("[ti]", _parameters(transport.calls[0][0])["term"][0])
        self.assertIn("[tiab]", _parameters(transport.calls[1][0])["term"][0])
        for call in transport.calls[:2]:
            self.assertIn("2020:2022[pdat]", _parameters(call[0])["term"][0])

    def test_author_and_identifier_searches_do_not_fall_back(self) -> None:
        searches = [
            lambda client: client.search_author("Lovelace A"),
            lambda client: client.lookup_doi("10.1234/example"),
            lambda client: client.lookup_pmid("12345"),
        ]

        for search in searches:
            with self.subTest(search=search):
                transport = FakeTransport([_esearch_response(0)])
                result = search(self._client(transport))

                self.assertFalse(result.fallback_used)
                self.assertEqual(len(transport.calls), 1)

    def test_pagination_uses_history_server_and_caps_page_size_at_twenty(self) -> None:
        transport = FakeTransport(
            [_esearch_response(45), _efetch_response(_article_xml())]
        )

        result = self._client(transport).search_title("sleep", page=2, page_size=20)

        parameters = _parameters(transport.calls[1][0])
        self.assertEqual(result.total_count, 45)
        self.assertEqual(parameters["query_key"], ["1"])
        self.assertEqual(parameters["WebEnv"], ["test-env"])
        self.assertEqual(parameters["retstart"], ["20"])
        self.assertEqual(parameters["retmax"], ["20"])

    def test_out_of_range_page_returns_no_records_without_efetch(self) -> None:
        transport = FakeTransport([_esearch_response(20)])

        result = self._client(transport).search_title("sleep", page=2, page_size=20)

        self.assertEqual(result.records, ())
        self.assertEqual(result.total_count, 20)
        self.assertEqual(len(transport.calls), 1)

    def test_rejects_invalid_pagination_and_year_ranges_before_requests(self) -> None:
        invalid_requests = [
            {"page": 0},
            {"page_size": 21},
            {"publication_year_end": 2024},
            {"publication_year": 2024, "publication_year_end": 2023},
        ]

        for request in invalid_requests:
            with self.subTest(request=request):
                transport = FakeTransport([])
                with self.assertRaises(PubMedRequestValidationError):
                    self._client(transport).search_title("sleep", **request)
                self.assertEqual(transport.calls, [])

    def test_blank_query_returns_empty_without_a_request(self) -> None:
        transport = FakeTransport([])

        result = self._client(transport).search_title(" \n\t ")

        self.assertEqual(result.records, ())
        self.assertEqual(result.total_count, 0)
        self.assertEqual(transport.calls, [])

    def test_plain_text_searches_reject_pubmed_query_syntax(self) -> None:
        searches = (
            lambda client, value: client.search_title(value),
            lambda client, value: client.search_title_abstract(value),
            lambda client, value: client.search_author(value),
        )
        query_syntax_inputs = (
            '"exact phrase"',
            "blood [pressure]",
            "heart failure[ti]",
            "heart OR lung",
        )

        for search in searches:
            for value in query_syntax_inputs:
                with self.subTest(value=value):
                    transport = FakeTransport([])
                    with self.assertRaises(PubMedRequestValidationError):
                        search(self._client(transport), value)
                    self.assertEqual(transport.calls, [])

    def test_plain_text_searches_preserve_common_literal_punctuation(self) -> None:
        transport = FakeTransport([_esearch_response(0), _esearch_response(0)])

        self._client(transport).search_title("COVID-19: a clinician's guide")

        term = _parameters(transport.calls[0][0])["term"][0]
        self.assertIn("COVID-19: a clinician's guide", term)

    def test_doi_and_pmid_lookups_validate_identifier_inputs(self) -> None:
        invalid_lookups = (
            (lambda client, value: client.lookup_doi(value), "not-a-doi"),
            (lambda client, value: client.lookup_doi(value), '10.1234/example"[ti]'),
            (lambda client, value: client.lookup_pmid(value), "12345[pmid]"),
            (lambda client, value: client.lookup_pmid(value), "12345 OR 67890"),
        )

        for lookup, value in invalid_lookups:
            with self.subTest(value=value):
                transport = FakeTransport([])
                with self.assertRaises(PubMedRequestValidationError):
                    lookup(self._client(transport), value)
                self.assertEqual(transport.calls, [])

    def test_doi_lookup_uses_a_quoted_valid_identifier(self) -> None:
        transport = FakeTransport([_esearch_response(0)])

        self._client(transport).lookup_doi("10.1234/example-value")

        self.assertEqual(
            _parameters(transport.calls[0][0])["term"],
            ['"10.1234/example-value"[aid]'],
        )

    def test_doi_lookup_retains_a_year_filter(self) -> None:
        transport = FakeTransport([_esearch_response(0)])

        self._client(transport).lookup_doi(
            "10.1234/example-value",
            publication_year=2020,
            publication_year_end=2022,
        )

        self.assertEqual(
            _parameters(transport.calls[0][0])["term"],
            ['"10.1234/example-value"[aid] AND 2020:2022[pdat]'],
        )

    def test_empty_esearch_result_does_not_trigger_efetch(self) -> None:
        transport = FakeTransport([_esearch_response(0)])

        result = self._client(transport).search_title_abstract("sleep")

        self.assertEqual(result.records, ())
        self.assertEqual(len(transport.calls), 1)

    def test_rejects_efetch_responses_without_citation_records(self) -> None:
        responses = (
            b"<eFetchResult></eFetchResult>",
            b"<PubmedArticleSet></PubmedArticleSet>",
        )

        for response in responses:
            with self.subTest(response=response):
                transport = FakeTransport([_esearch_response(1), response])
                with self.assertRaises(PubMedResponseError):
                    self._client(transport).search_title_abstract("sleep")

    def test_maps_pubmed_record_to_validated_metadata_without_abstract(self) -> None:
        transport = FakeTransport(
            [
                _esearch_response(1),
                _efetch_response(_article_xml(abstract="Publisher supplied abstract")),
            ]
        )

        result = self._client(transport).lookup_pmid("12345")
        record = result.records[0]

        self.assertIsInstance(record.metadata, PaperMetadata)
        self.assertEqual(record.metadata.source_identifier, "PMID:12345")
        self.assertEqual(
            record.metadata.source_url, "https://pubmed.ncbi.nlm.nih.gov/12345/"
        )
        self.assertIsNone(record.metadata.abstract)
        self.assertEqual(record.doi, "10.1234/example")

    def test_missing_abstract_does_not_invalidate_metadata(self) -> None:
        transport = FakeTransport(
            [_esearch_response(1), _efetch_response(_article_xml())]
        )

        result = self._client(transport).lookup_pmid("12345")

        self.assertEqual(len(result.records), 1)
        self.assertIsNone(result.records[0].metadata.abstract)

    def test_primary_journal_year_wins_over_electronic_and_history_dates(self) -> None:
        transport = FakeTransport(
            [
                _esearch_response(1),
                _efetch_response(
                    _article_xml(year="2021", article_year="2020", history_year="2019")
                ),
            ]
        )

        result = self._client(transport).lookup_pmid("12345")

        self.assertEqual(result.records[0].metadata.publication_year, 2021)
        self.assertEqual(result.records[0].citation_date, "2021")

    def test_uses_first_medline_date_year_only_when_year_is_absent(self) -> None:
        transport = FakeTransport(
            [
                _esearch_response(1),
                _efetch_response(_article_xml(year=None, medline_date="1998 Dec-1999 Jan")),
            ]
        )

        result = self._client(transport).lookup_pmid("12345")

        self.assertEqual(result.records[0].metadata.publication_year, 1998)
        self.assertEqual(result.records[0].citation_date, "1998 Dec-1999 Jan")

    def test_skips_missing_or_unusable_primary_citation_dates(self) -> None:
        transport = FakeTransport(
            [
                _esearch_response(2),
                _efetch_response(
                    _article_xml(pmid="1", year=None, medline_date="Spring")
                    + _article_xml(pmid="2", year=None)
                ),
            ]
        )

        result = self._client(transport).search_title_abstract("sleep")

        self.assertEqual(result.records, ())
        self.assertEqual(result.invalid_record_count, 2)

    def test_skips_malformed_journal_year_without_medline_date_fallback(self) -> None:
        transport = FakeTransport(
            [
                _esearch_response(1),
                _efetch_response(
                    _article_xml(year="20X4", medline_date="2024 Jan", article_year="2023")
                ),
            ]
        )

        result = self._client(transport).lookup_pmid("12345")

        self.assertEqual(result.records, ())
        self.assertEqual(result.invalid_record_count, 1)

    def test_skips_invalid_citation_and_keeps_valid_siblings(self) -> None:
        transport = FakeTransport(
            [
                _esearch_response(2),
                _efetch_response(
                    _article_xml(pmid="1", title="Valid citation")
                    + _article_xml(pmid="2", include_author=False)
                ),
            ]
        )

        result = self._client(transport).search_title_abstract("sleep")

        self.assertEqual([record.pmid for record in result.records], ["1"])
        self.assertEqual(result.invalid_record_count, 1)

    def test_translates_timeout_http_transport_and_malformed_xml_failures(self) -> None:
        cases: list[tuple[Exception | bytes, type[Exception]]] = [
            (socket.timeout(), PubMedTimeoutError),
            (
                HTTPError("https://example.org", 503, "Service unavailable", None, BytesIO()),
                PubMedHttpError,
            ),
            (URLError("offline"), PubMedTransportError),
            (b"<eSearchResult>", PubMedResponseError),
        ]

        for response, error_type in cases:
            with self.subTest(error_type=error_type):
                transport = FakeTransport([response])
                with self.assertRaises(error_type):
                    self._client(transport).search_title_abstract("sleep")

    def test_translates_malformed_efetch_xml(self) -> None:
        transport = FakeTransport([_esearch_response(1), b"<PubmedArticleSet>"])

        with self.assertRaises(PubMedResponseError):
            self._client(transport).search_title_abstract("sleep")

    def test_adds_identification_and_api_key_parameters(self) -> None:
        transport = FakeTransport([_esearch_response(0)])
        client = self._client(transport, api_key="secret-key")

        client.search_author("Lovelace A")

        parameters = _parameters(transport.calls[0][0])
        self.assertEqual(parameters["tool"], ["healthcare_research_intelligence"])
        self.assertEqual(parameters["email"], ["developer@example.org"])
        self.assertEqual(parameters["api_key"], ["secret-key"])
        self.assertEqual(transport.calls[0][1], 4.0)

    def test_rate_limiter_uses_injected_clock_and_sleeper(self) -> None:
        transport = FakeTransport([_esearch_response(1), _efetch_response(_article_xml())])
        sleep_calls: list[float] = []
        client = PubMedClient(
            self._config(requests_per_second=1),
            transport=transport,
            clock=lambda: 0.0,
            sleeper=sleep_calls.append,
        )

        client.search_title_abstract("sleep")

        self.assertEqual(sleep_calls, [1.0])

    def test_rate_limiter_serialises_concurrent_slot_reservations(self) -> None:
        class BlockingSleeper:
            def __init__(self) -> None:
                self.calls: list[float] = []
                self.first_sleep_started = Event()
                self.release_first_sleep = Event()

            def __call__(self, seconds: float) -> None:
                self.calls.append(seconds)
                if len(self.calls) == 1:
                    self.first_sleep_started.set()
                    self.release_first_sleep.wait(timeout=1)

        sleeper = BlockingSleeper()
        client = PubMedClient(
            self._config(requests_per_second=1),
            transport=FakeTransport([]),
            clock=lambda: 0.0,
            sleeper=sleeper,
        )
        client._wait_for_request_slot()

        first = Thread(target=client._wait_for_request_slot)
        first.start()
        self.assertTrue(sleeper.first_sleep_started.wait(timeout=1))

        lock_acquired = client._rate_limit_lock.acquire(blocking=False)
        if lock_acquired:
            client._rate_limit_lock.release()
        self.assertFalse(lock_acquired)

        second_started = Event()

        def reserve_second_slot() -> None:
            second_started.set()
            client._wait_for_request_slot()

        second = Thread(target=reserve_second_slot)
        second.start()
        self.assertTrue(second_started.wait(timeout=1))

        sleeper.release_first_sleep.set()
        first.join(timeout=1)
        second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(sleeper.calls, [1.0, 2.0])


class PubMedClientConfigTests(unittest.TestCase):
    """Verify configuration validation stays separate from client logic."""

    def test_requires_tool_and_developer_email(self) -> None:
        with self.assertRaises(PubMedConfigurationError):
            PubMedClientConfig(tool="", email="developer@example.org")
        with self.assertRaises(PubMedConfigurationError):
            PubMedClientConfig(tool="tool", email="")

    def test_loads_optional_api_key_from_environment(self) -> None:
        config = PubMedClientConfig.from_environment(
            {
                "PUBMED_NCBI_TOOL": "healthcare_research_intelligence",
                "PUBMED_NCBI_EMAIL": "developer@example.org",
                "PUBMED_NCBI_API_KEY": "secret-key",
                "PUBMED_TIMEOUT_SECONDS": "8",
            }
        )

        self.assertEqual(config.api_key, "secret-key")
        self.assertEqual(config.timeout_seconds, 8.0)
        self.assertEqual(config.requests_per_second, 10.0)

    def test_rejects_non_finite_timeout_and_request_rate_values(self) -> None:
        for field_name in ("timeout_seconds", "requests_per_second"):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field_name=field_name, value=value):
                    with self.assertRaises(PubMedConfigurationError):
                        PubMedClientConfig(
                            tool="healthcare_research_intelligence",
                            email="developer@example.org",
                            **{field_name: value},
                        )

    def test_requires_the_official_secure_eutilities_host(self) -> None:
        untrusted_base_urls = (
            "http://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
            "https://example.org/entrez/eutils/",
            "https://eutils.ncbi.nlm.nih.gov.example.org/entrez/eutils/",
            "https://eutils.ncbi.nlm.nih.gov@example.org/entrez/eutils/",
        )

        for base_url in untrusted_base_urls:
            with self.subTest(base_url=base_url):
                with self.assertRaises(PubMedConfigurationError):
                    PubMedClientConfig(
                        tool="healthcare_research_intelligence",
                        email="developer@example.org",
                        base_url=base_url,
                    )


if __name__ == "__main__":
    unittest.main()
