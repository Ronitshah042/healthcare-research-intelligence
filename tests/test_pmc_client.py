"""Tests for the PMC client, using a fake transport and recorded fixtures."""

from __future__ import annotations

from io import BytesIO
import json
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from ingestion.pmc_client import (
    ArticleIds,
    PmcClient,
    PmcHttpError,
    PmcRequestValidationError,
    PmcResponseError,
    article_page_url,
    article_xml_url,
)
from ingestion.pubmed import PubMedClient
from ingestion.pubmed_config import PubMedClientConfig


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pmc"


class RoutingTransport:
    """Answer by URL path; remember every URL requested."""

    def __init__(self, routes: dict[str, list[bytes | Exception]]) -> None:
        self.routes = {path: list(responses) for path, responses in routes.items()}
        self.urls: list[str] = []

    def __call__(self, url: str, timeout_seconds: float) -> bytes:
        self.urls.append(url)
        parts = urlsplit(url)
        key = f"{parts.netloc}{parts.path}"
        if key not in self.routes or not self.routes[key]:
            raise HTTPError(url, 404, "Not Found", None, BytesIO())
        response = self.routes[key].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def converter_response(*records: dict, status: str = "ok") -> bytes:
    return json.dumps({"status": status, "records": list(records)}).encode()


def converter_record(pmcid: str, *, pmid: int | None = 1, current: str | None = None,
                     requested: str | None = None) -> dict:
    record = {"pmcid": pmcid, "pmid": pmid, "doi": "10.1234/x",
              "requested-id": requested or pmcid}
    if current:
        record["versions"] = [
            {"pmcid": f"{pmcid}.9", "current": False},
            {"pmcid": current, "current": True},
        ]
    return record


IDCONV = "pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
ESEARCH = "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
CLOUD = "pmc-oa-opendata.s3.amazonaws.com/"
EFETCH = "eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def pubmed_article(pmid: str, *ref_types: str) -> str:
    links = "".join(
        f'<CommentsCorrections RefType="{ref_type}"><PMID>9{pmid}</PMID>'
        "</CommentsCorrections>"
        for ref_type in ref_types
    )
    return (
        f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID>"
        f"<CommentsCorrectionsList>{links}</CommentsCorrectionsList>"
        "</MedlineCitation></PubmedArticle>"
    )


class PmcClientTestCase(unittest.TestCase):
    def setUp(self) -> None:
        PubMedClient._reset_process_rate_limiters_for_tests()

    def tearDown(self) -> None:
        PubMedClient._reset_process_rate_limiters_for_tests()

    @staticmethod
    def config(**overrides: object) -> PubMedClientConfig:
        values = {"tool": "hri", "email": "dev@example.org", **overrides}
        return PubMedClientConfig(**values)  # type: ignore[arg-type]

    def client(self, routes, **config) -> tuple[PmcClient, RoutingTransport]:
        transport = RoutingTransport(routes)
        client = PmcClient(
            self.config(**config), transport=transport,
            clock=lambda: 0.0, sleeper=lambda seconds: None,
        )
        return client, transport


class SearchTests(PmcClientTestCase):
    def test_searches_pmc_for_open_access_articles(self) -> None:
        response = b"<eSearchResult><Count>2</Count><IdList><Id>101</Id><Id>7</Id></IdList></eSearchResult>"
        client, transport = self.client({ESEARCH: [response]}, api_key="secret")

        pmcids = client.search_open_access("heart failure", max_results=5)

        self.assertEqual(pmcids, ("PMC101", "PMC7"))
        query = parse_qs(urlsplit(transport.urls[0]).query)
        self.assertEqual(query["db"], ["pmc"])
        self.assertEqual(query["term"], ["(heart failure) AND open access[filter]"])
        self.assertEqual(query["retmax"], ["5"])
        self.assertEqual((query["tool"], query["email"], query["api_key"]),
                         (["hri"], ["dev@example.org"], ["secret"]))

    def test_rejects_query_syntax_and_bad_limits_before_any_request(self) -> None:
        client, transport = self.client({})
        for query, limit in (("a OR b", 5), ("x[ti]", 5), ("  ", 5), ("ok", 0),
                             ("ok", 201), ("ok", True)):
            with self.subTest(query=query, limit=limit):
                with self.assertRaises(PmcRequestValidationError):
                    client.search_open_access(query, max_results=limit)
        self.assertEqual(transport.urls, [])

    def test_reports_search_errors_without_server_text(self) -> None:
        cases = (
            b"<eSearchResult><ERROR>secret detail</ERROR></eSearchResult>",
            b"<eSearchResult><IdList><Id>abc</Id></IdList></eSearchResult>",
            b"not xml",
        )
        for response in cases:
            with self.subTest(response=response):
                client, _ = self.client({ESEARCH: [response]})
                with self.assertRaises(PmcResponseError) as raised:
                    client.search_open_access("sleep")
                self.assertNotIn("secret", str(raised.exception))


class ConverterTests(PmcClientTestCase):
    def test_finds_identifiers_and_the_current_version(self) -> None:
        client, transport = self.client({IDCONV: [converter_response(
            converter_record("PMC5", current="PMC5.2", requested="5"),
            {"requested-id": "6", "errmsg": "invalid article id"},
        )]})

        found = client.convert_ids(["5", "006"], id_type="pmid")

        self.assertEqual(found, {"5": ArticleIds("5", "PMC5", "1", "10.1234/x", "PMC5.2")})
        query = parse_qs(urlsplit(transport.urls[0]).query)
        self.assertEqual(query["ids"], ["5,6"])
        self.assertEqual((query["idtype"], query["versions"], query["format"]),
                         (["pmid"], ["yes"], ["json"]))
        self.assertEqual((query["tool"], query["email"]), (["hri"], ["dev@example.org"]))

    def test_no_api_key_is_sent_to_the_id_converter(self) -> None:
        client, transport = self.client({IDCONV: [converter_response()]}, api_key="secret")

        client.convert_ids(["PMC1"], id_type="pmcid")

        self.assertNotIn("secret", transport.urls[0])

    def test_a_version_marked_current_is_required(self) -> None:
        record = converter_record("PMC5")
        record["versions"] = [{"pmcid": "PMC5.1", "current": False}]
        client, _ = self.client({IDCONV: [converter_response(record)]})

        self.assertIsNone(client.convert_ids(["PMC5"], id_type="pmcid")["PMC5"].current_version)

    def test_sends_at_most_200_ids_per_request(self) -> None:
        client, transport = self.client(
            {IDCONV: [converter_response(), converter_response()]}
        )

        client.convert_ids([f"PMC{n}" for n in range(1, 202)], id_type="pmcid")

        sizes = [len(parse_qs(urlsplit(url).query)["ids"][0].split(",")) for url in transport.urls]
        self.assertEqual(sizes, [200, 1])

    def test_validates_ids_and_reports_converter_errors(self) -> None:
        client, transport = self.client({})
        for ids, id_type in ((["12a"], "pmid"), (["PMCx"], "pmcid"),
                             (["not-a-doi"], "doi"), (["10.1/a,b"], "doi"),
                             ([""], "pmid"), (["1"], "mid")):
            with self.subTest(ids=ids, id_type=id_type):
                with self.assertRaises(PmcRequestValidationError):
                    client.convert_ids(ids, id_type=id_type)
        self.assertEqual(transport.urls, [])
        for response in (converter_response(status="error"), b"{", b"[]"):
            with self.subTest(response=response):
                client, _ = self.client({IDCONV: [response]})
                with self.assertRaises(PmcResponseError):
                    client.convert_ids(["PMC1"], id_type="pmcid")


class CloudTests(PmcClientTestCase):
    def test_reads_licence_metadata_from_the_cloud_service(self) -> None:
        raw = (FIXTURES / "PMC10009402.1.json").read_bytes()
        client, transport = self.client(
            {CLOUD + "metadata/PMC10009402.1.json": [raw]}, api_key="secret"
        )

        metadata = client.cloud_metadata("PMC10009402.1")

        self.assertEqual(
            (metadata.licence, metadata.pmid, metadata.doi, metadata.is_open_access,
             metadata.is_retracted, metadata.is_manuscript),
            ("CC BY", "35275515", "10.1017/S0033291721002701", True, False, False),
        )
        self.assertEqual(
            transport.urls,
            ["https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC10009402.1.json"],
        )

    def test_missing_version_returns_none(self) -> None:
        client, _ = self.client({})

        self.assertIsNone(client.cloud_metadata("PMC1.1"))

    def test_unclear_status_fields_fall_on_the_safe_side(self) -> None:
        payload = {"pmcid": "PMC1", "version": 1, "license_code": None}
        client, _ = self.client({CLOUD + "metadata/PMC1.1.json": [json.dumps(payload).encode()]})

        metadata = client.cloud_metadata("PMC1.1")

        self.assertEqual((metadata.licence, metadata.is_open_access, metadata.is_retracted),
                         (None, False, True))

    def test_rejects_metadata_for_a_different_article(self) -> None:
        payload = {"pmcid": "PMC2", "version": 1}
        for response in (json.dumps(payload).encode(), b"nope", b"[1]"):
            with self.subTest(response=response):
                client, _ = self.client({CLOUD + "metadata/PMC1.1.json": [response]})
                with self.assertRaises(PmcResponseError):
                    client.cloud_metadata("PMC1.1")

    def test_downloads_article_xml_without_credentials(self) -> None:
        xml = (FIXTURES / "PMC10009402.1.xml").read_bytes()
        client, transport = self.client(
            {CLOUD + "PMC10009402.1/PMC10009402.1.xml": [xml]}, api_key="secret"
        )

        self.assertEqual(client.article_xml("PMC10009402.1"), xml)
        self.assertEqual(transport.urls, [article_xml_url("PMC10009402.1")])
        self.assertNotIn("?", transport.urls[0])

    def test_validates_versions_and_builds_public_links(self) -> None:
        client, transport = self.client({})
        for value in ("PMC1", "1.1", "PMC1.1/../x", None):
            with self.subTest(value=value):
                with self.assertRaises(PmcRequestValidationError):
                    client.article_xml(value)  # type: ignore[arg-type]
        self.assertEqual(transport.urls, [])
        self.assertEqual(article_page_url("PMC5.2"), "https://pmc.ncbi.nlm.nih.gov/articles/PMC5/")


class PubMedNoticeTests(PmcClientTestCase):
    def test_maps_pubmed_correction_links_to_notice_flags(self) -> None:
        response = (
            "<PubmedArticleSet>"
            + pubmed_article("1", "ErratumIn", "CommentIn", "ErratumIn")
            + pubmed_article("2", "ExpressionOfConcernIn", "PartialRetractionIn")
            + pubmed_article("3", "RetractionIn")
            + pubmed_article("4", "CommentIn", "Cites")
            + pubmed_article("77", "RetractionIn")  # not requested: ignored
            + "</PubmedArticleSet>"
        ).encode()
        client, transport = self.client({EFETCH: [response]}, api_key="secret")

        notices = client.pubmed_notices(["1", "002", "3", "4"])

        self.assertEqual(notices, {
            "1": ("corrected",),
            "2": ("expression_of_concern", "partially_retracted"),
            "3": ("retracted",),
        })
        query = parse_qs(urlsplit(transport.urls[0]).query)
        self.assertEqual((query["db"], query["id"]), (["pubmed"], ["1,2,3,4"]))
        self.assertEqual(query["api_key"], ["secret"])

    def test_sends_no_request_for_no_pmids_and_batches_by_200(self) -> None:
        empty = b"<PubmedArticleSet></PubmedArticleSet>"
        client, transport = self.client({EFETCH: [empty, empty]})

        self.assertEqual(client.pubmed_notices([]), {})
        self.assertEqual(transport.urls, [])
        client.pubmed_notices(str(number) for number in range(1, 251))
        self.assertEqual(len(transport.urls), 2)

    def test_rejects_bad_pmids_and_reports_errors_safely(self) -> None:
        client, transport = self.client({})
        with self.assertRaises(PmcRequestValidationError):
            client.pubmed_notices(["12a"])
        self.assertEqual(transport.urls, [])
        for response in (b"<eFetchResult><ERROR>secret</ERROR></eFetchResult>", b"<x"):
            with self.subTest(response=response):
                client, _ = self.client({EFETCH: [response]})
                with self.assertRaises(PmcResponseError) as raised:
                    client.pubmed_notices(["1"])
                self.assertNotIn("secret", str(raised.exception))


class SafetyTests(PmcClientTestCase):
    def test_only_allowlisted_https_hosts_are_contacted(self) -> None:
        client, transport = self.client({})
        for url in (
            "http://pmc-oa-opendata.s3.amazonaws.com/x",
            "https://example.org/x",
            "https://pmc-oa-opendata.s3.amazonaws.com.evil.org/x",
            "https://user@pmc.ncbi.nlm.nih.gov/x",
        ):
            with self.subTest(url=url):
                with self.assertRaises(PmcRequestValidationError):
                    client._get(url)
        self.assertEqual(transport.urls, [])

    def test_retries_transient_failures_and_redacts_errors(self) -> None:
        busy = HTTPError("https://x/?api_key=secret", 503, "Busy", None, BytesIO())
        xml_path = CLOUD + "PMC1.1/PMC1.1.xml"
        client, transport = self.client({xml_path: [busy, b"<article/>"]})

        self.assertEqual(client.article_xml("PMC1.1"), b"<article/>")
        self.assertEqual(len(transport.urls), 2)

        client, _ = self.client({xml_path: [busy, busy, busy]})
        with self.assertRaises(PmcHttpError) as raised:
            client.article_xml("PMC1.1")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn("secret", str(raised.exception))

    def test_ncbi_requests_share_the_pubmed_rate_limit(self) -> None:
        client, _ = self.client({})
        pubmed = PubMedClient(self.config())

        self.assertIs(client._ncbi.rate_limiter.lock, pubmed._rate_limit_lock)
        self.assertIsNot(client._cloud.rate_limiter, client._ncbi.rate_limiter)


if __name__ == "__main__":
    unittest.main()
