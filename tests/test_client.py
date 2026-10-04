from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from faa_drs import (
    API_KEY_ENV,
    APIError,
    Attachment,
    AuthenticationError,
    BadRequestError,
    DocType,
    DRSClient,
    DRSConnectionError,
    DRSError,
    DRSTimeoutError,
    InvalidQueryError,
    NotFoundError,
    RateLimitError,
    ResponseValidationError,
    RestrictedDocTypeError,
    ServerError,
    UnknownDocTypeError,
)
from tests.fake_drs import API_KEY, BASE_URL, FakeDRS, make_doc


def test_list_documents(client: DRSClient, fake: FakeDRS) -> None:
    page = client.list_documents(DocType.SAIB)
    assert page.doctype == "SAIB"
    assert page.total == len(fake.documents["SAIB"])
    assert [d.guid for d in page.documents] == [d["documentGuid"] for d in fake.documents["SAIB"]]
    request = fake.requests[-1]
    assert request.method == "GET"
    assert request.url.path == "/api/drs/data-pull/SAIB"
    assert request.headers["x-api-key"] == API_KEY
    assert request.headers["user-agent"].startswith("faa-drs/")


def test_list_documents_with_offset(client: DRSClient) -> None:
    page = client.list_documents("BULK", offset=20)
    assert page.offset == 20
    assert len(page.documents) == 5
    assert page.next_offset is None


def test_filters_use_filtered_endpoint(client: DRSClient, fake: FakeDRS) -> None:
    page = client.list_documents("SAIB", filters={"drs:status": "Current"}, keywords="Boeing")
    request = fake.requests[-1]
    assert request.method == "POST"
    assert request.url.path == "/api/drs/data-pull/SAIB/filtered"
    assert json.loads(request.content)["documentFilters"] == {
        "drs:status": ["Current"],
        "Keyword": ["Boeing"],
    }
    assert all(d.status == "Current" for d in page.documents)


def test_invalid_query_sends_no_request(client: DRSClient, fake: FakeDRS) -> None:
    with pytest.raises(InvalidQueryError):
        client.list_documents("SAIB", filters={"drs:nope": "x"})
    assert fake.requests == []


def test_iter_pages_walks_all_offsets(client: DRSClient, fake: FakeDRS) -> None:
    pages = list(client.iter_pages("BULK"))
    assert [p.offset for p in pages] == [0, 10, 20]
    assert [r.url.params["offset"] for r in fake.requests] == ["0", "10", "20"]
    assert all(r.url.params["docLastModifiedDateSortOrder"] == "DESC" for r in fake.requests)


def test_iter_documents_sorts_newest_first_by_default(client: DRSClient) -> None:
    docs = list(client.iter_documents("BULK"))
    assert len(docs) == 25
    assert len({d.guid for d in docs}) == 25
    stamps = [d.last_modified for d in docs if d.last_modified]
    assert len(stamps) == 25
    assert stamps == sorted(stamps, reverse=True)


def test_desc_puts_documents_without_a_date_last(client: DRSClient, fake: FakeDRS) -> None:
    fake.documents["MIXED"] = [
        make_doc(i, modified=None if i % 2 else f"2024-01-0{i + 1}T00:00:00.000Z") for i in range(6)
    ]
    fake.page_size = 4
    dates = [d.last_modified for d in client.iter_documents("MIXED")]
    assert dates[3:] == [None, None, None]
    assert all(dates[:3])


def test_iter_documents_api_default_order(client: DRSClient, fake: FakeDRS) -> None:
    docs = list(client.iter_documents("BULK", sort=None))
    assert [d.guid for d in docs] == [d["documentGuid"] for d in fake.documents["BULK"]]
    assert "docLastModifiedDateSortOrder" not in fake.requests[0].url.params


def test_iter_documents_limit_stops_requests(client: DRSClient, fake: FakeDRS) -> None:
    assert len(list(client.iter_documents("BULK", limit=12))) == 12
    assert len(fake.requests) == 2
    assert list(client.iter_documents("BULK", limit=0)) == []
    assert len(fake.requests) == 2


def test_iter_documents_resumes_from_offset(client: DRSClient) -> None:
    all_docs = list(client.iter_documents("BULK"))
    resumed = list(client.iter_documents("BULK", offset=15))
    assert resumed == all_docs[15:]


def test_incremental_sync_with_modified_after(client: DRSClient) -> None:
    docs = list(client.iter_documents("BULK"))
    checkpoint = docs[19].last_modified
    assert checkpoint is not None
    newer = list(client.iter_documents("BULK", modified_after=checkpoint))
    assert newer
    assert all(d.last_modified and d.last_modified > checkpoint for d in newer)


def test_empty_result(client: DRSClient, fake: FakeDRS) -> None:
    fake.documents["EMPTY"] = []
    assert list(client.iter_documents("EMPTY")) == []
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    ("doctype", "error"),
    [("NOPE", UnknownDocTypeError), ("ICAO_ANNEX", RestrictedDocTypeError)],
)
def test_errors_in_http_200_bodies(client: DRSClient, doctype: str, error: type[APIError]) -> None:
    with pytest.raises(error, match=doctype) as info:
        client.list_documents(doctype)
    assert info.value.status_code == 200


def test_bad_api_key(fake: FakeDRS, sleeps: list[float]) -> None:
    http = httpx.Client(transport=fake.transport())
    with (
        DRSClient("wrong", http_client=http) as drs,
        pytest.raises(AuthenticationError, match="API key"),
    ):
        drs.list_documents("SAIB")
    assert len(fake.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            httpx.Response(400, json={"errorMessage": "One or more filters provided are invalid"}),
            BadRequestError,
        ),
        (httpx.Response(404, json={"error": "Not Found"}), NotFoundError),
        (httpx.Response(418, text="teapot"), APIError),
    ],
)
def test_client_errors_are_not_retried(
    client: DRSClient,
    fake: FakeDRS,
    sleeps: list[float],
    response: httpx.Response,
    error: type[APIError],
) -> None:
    fake.fail_next(response)
    with pytest.raises(error) as info:
        client.list_documents("SAIB")
    assert info.value.status_code == response.status_code
    assert info.value.response is not None
    assert sleeps == []


def test_server_errors_are_retried(client: DRSClient, fake: FakeDRS, sleeps: list[float]) -> None:
    fake.fail_next(httpx.Response(504), httpx.Response(500, text="boom"))
    page = client.list_documents("SAIB")
    assert page.documents
    assert len(fake.requests) == 3
    assert sleeps == [0.01, 0.02]


def test_system_error_message_is_retried(client: DRSClient, fake: FakeDRS) -> None:
    fake.fail_next(
        httpx.Response(
            200,
            json={"errorMessage": "Unable to retrieve documents due to system error, try later"},
        )
    )
    assert client.list_documents("SAIB").documents
    assert len(fake.requests) == 2


def test_retries_are_bounded(client: DRSClient, fake: FakeDRS, sleeps: list[float]) -> None:
    fake.fail_next(*[httpx.Response(503) for _ in range(10)])
    with pytest.raises(ServerError) as info:
        client.list_documents("SAIB")
    assert info.value.status_code == 503
    assert len(fake.requests) == 4
    assert len(sleeps) == 3


def test_rate_limit_honors_retry_after(
    client: DRSClient, fake: FakeDRS, sleeps: list[float]
) -> None:
    fake.fail_next(httpx.Response(429, headers={"retry-after": "7"}))
    client.list_documents("SAIB")
    assert sleeps == [7.0]


def test_rate_limit_error_after_retries(fake: FakeDRS, sleeps: list[float]) -> None:
    http = httpx.Client(transport=fake.transport())
    fake.fail_next(httpx.Response(429), httpx.Response(429))
    with DRSClient(API_KEY, http_client=http, max_retries=1) as drs, pytest.raises(RateLimitError):
        drs.list_documents("SAIB")


def test_max_retries_zero(fake: FakeDRS, sleeps: list[float]) -> None:
    http = httpx.Client(transport=fake.transport())
    fake.fail_next(httpx.Response(500))
    with DRSClient(API_KEY, http_client=http, max_retries=0) as drs, pytest.raises(ServerError):
        drs.list_documents("SAIB")
    assert sleeps == []


def _raise(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def test_connection_errors_are_retried(client: DRSClient, fake: FakeDRS) -> None:
    fake.fail_next(_raise(httpx.ConnectError("refused")), _raise(httpx.ReadTimeout("slow")))
    assert client.list_documents("SAIB").documents
    assert len(fake.requests) == 3


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (httpx.ConnectError("refused"), DRSConnectionError),
        (httpx.ReadTimeout("slow"), DRSTimeoutError),
    ],
)
def test_transport_errors_are_wrapped(
    fake: FakeDRS, sleeps: list[float], exc: Exception, error: type[Exception]
) -> None:
    http = httpx.Client(transport=fake.transport())
    fake.fail_next(_raise(exc))
    with DRSClient(API_KEY, http_client=http, max_retries=0) as drs, pytest.raises(error) as info:
        drs.list_documents("SAIB")
    assert info.value.__cause__ is exc


def test_unexpected_shape_raises_response_validation_error(
    client: DRSClient, fake: FakeDRS
) -> None:
    fake.fail_next(httpx.Response(200, json={"summary": {"doctypeName": "SAIB"}, "documents": []}))
    with pytest.raises(ResponseValidationError, match="Page"):
        client.list_documents("SAIB")


def test_non_json_body_raises_response_validation_error(client: DRSClient, fake: FakeDRS) -> None:
    fake.fail_next(httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(ResponseValidationError, match="Expected JSON"):
        client.list_documents("SAIB")


def test_get_attachments(client: DRSClient, fake: FakeDRS) -> None:
    doc = next(client.iter_documents("BULK", sort=None, offset=1, limit=1))
    doc = doc.model_copy(update={"has_attachments": True})
    attachments = client.get_attachments(doc)
    assert attachments == [
        Attachment(file_name="Appendix A.pdf", download_url=f"{BASE_URL}/download/att-1")
    ]
    assert fake.requests[-1].url.path.endswith("/get-other-attachment-details/file-00001")


def test_get_attachments_skips_request_when_flag_false(client: DRSClient, fake: FakeDRS) -> None:
    doc = next(client.iter_documents("BULK", limit=1))
    count = len(fake.requests)
    assert client.get_attachments(doc) == []
    assert len(fake.requests) == count


def test_get_attachments_comments_response_is_empty(client: DRSClient) -> None:
    assert client.get_attachments("file-99999") == []


def test_download_bytes(client: DRSClient) -> None:
    doc = next(client.iter_documents("BULK", sort=None, offset=1, limit=1))
    assert client.download(doc) == b"%PDF-1.7 main"
    assert client.download(f"{BASE_URL}/download/att-1") == b"%PDF-1.7 attachment"
    assert client.download("att-1") == b"%PDF-1.7 attachment"


def test_download_missing_file(client: DRSClient) -> None:
    with pytest.raises(NotFoundError):
        client.download("nope")


def test_download_document_without_file(client: DRSClient) -> None:
    doc = next(client.iter_documents("PMA", limit=1))
    with pytest.raises(DRSError, match="no downloadable file"):
        client.download(doc)


def test_download_never_sends_key_to_foreign_host(client: DRSClient, fake: FakeDRS) -> None:
    client.download("https://evil.example.com/steal/att-1")
    assert fake.requests[-1].url.host == "drs.faa.gov"


@pytest.mark.parametrize("bad", ["..", ".hidden", "a b", "a?b", ""])
def test_invalid_file_ids_are_rejected(client: DRSClient, fake: FakeDRS, bad: str) -> None:
    with pytest.raises(DRSError, match="Invalid file id"):
        client.download(bad)
    assert fake.requests == []


def test_download_to_directory_uses_document_file_name(client: DRSClient, tmp_path: Path) -> None:
    doc = next(client.iter_documents("BULK", sort=None, offset=1, limit=1))
    path = client.download_to(doc, tmp_path)
    assert path == tmp_path / "DOC-00001.pdf"
    assert path.read_bytes() == b"%PDF-1.7 main"
    assert list(tmp_path.iterdir()) == [path]


def test_download_to_directory_uses_content_disposition_for_ids(
    client: DRSClient, tmp_path: Path
) -> None:
    assert client.download_to("att-1", tmp_path).name == "GUID.0001.att-1.pdf"


def test_download_to_file_path_creates_parents(client: DRSClient, tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "file.pdf"
    attachment = Attachment(file_name="x.pdf", download_url=f"{BASE_URL}/download/att-1")
    assert client.download_to(attachment, target) == target
    assert target.read_bytes() == b"%PDF-1.7 attachment"


def test_download_to_leaves_no_partial_file(
    client: DRSClient, fake: FakeDRS, tmp_path: Path
) -> None:
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("dropped")

    fake.fail_next(*[lambda r: httpx.Response(200, stream=Broken()) for _ in range(4)])
    with pytest.raises(DRSConnectionError):
        client.download_to("att-1", tmp_path / "out.pdf")
    assert list(tmp_path.iterdir()) == []


def test_download_retries_stream_failure(client: DRSClient, fake: FakeDRS, tmp_path: Path) -> None:
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadError("dropped")
            yield b""

    fake.fail_next(lambda r: httpx.Response(200, stream=Broken()))
    path = client.download_to("att-1", tmp_path / "out.pdf")
    assert path.read_bytes() == b"%PDF-1.7 attachment"


def test_redirect_keeps_key_on_same_origin(client: DRSClient, fake: FakeDRS) -> None:
    fake.fail_next(httpx.Response(302, headers={"location": f"{BASE_URL}/download/att-1"}))
    assert client.download("other") == b"%PDF-1.7 attachment"
    assert fake.requests[-1].headers["x-api-key"] == API_KEY


def test_redirect_drops_key_on_other_origin(client: DRSClient, fake: FakeDRS) -> None:
    fake.fail_next(
        httpx.Response(302, headers={"location": "https://files.example.com/x.pdf"}),
        httpx.Response(200, content=b"cdn"),
    )
    assert client.download("att-1") == b"cdn"
    assert fake.requests[-1].url.host == "files.example.com"
    assert "x-api-key" not in fake.requests[-1].headers


def test_api_key_from_environment(monkeypatch: pytest.MonkeyPatch, fake: FakeDRS) -> None:
    monkeypatch.setenv(API_KEY_ENV, f"  {API_KEY}\n")
    with DRSClient(http_client=httpx.Client(transport=fake.transport())) as drs:
        drs.list_documents("SAIB")
    assert fake.requests[-1].headers["x-api-key"] == API_KEY


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(DRSError, match=API_KEY_ENV):
        DRSClient()
    with pytest.raises(ValueError, match="max_retries"):
        DRSClient("key", max_retries=-1)


def test_external_http_client_is_not_closed(fake: FakeDRS) -> None:
    http = httpx.Client(transport=fake.transport())
    with DRSClient(API_KEY, http_client=http):
        pass
    assert not http.is_closed
    http.close()


def test_owned_http_client_is_closed() -> None:
    drs = DRSClient("key")
    drs.close()
    assert drs._client.is_closed


def test_repr_hides_key() -> None:
    drs = DRSClient("secret-key", base_url="https://example.com/api/")
    assert repr(drs) == "DRSClient(base_url='https://example.com/api')"
    drs.close()


def test_custom_base_url(fake: FakeDRS) -> None:
    fake.documents["X"] = [make_doc(1)]
    with DRSClient(
        API_KEY,
        base_url="https://drs.faa.gov/api/drs/data-pull/",
        http_client=httpx.Client(transport=fake.transport()),
    ) as drs:
        assert drs.list_documents("X").total == 1


def test_too_many_redirects(client: DRSClient, fake: FakeDRS) -> None:
    loop = httpx.Response(302, headers={"location": f"{BASE_URL}/download/loop"})
    fake.fail_next(*[loop] * 10)
    with pytest.raises(APIError, match="redirects") as info:
        client.download("att-1")
    assert info.value.status_code == 302
    assert len(fake.requests) == 6


def test_filtered_query_survives_307(client: DRSClient, fake: FakeDRS) -> None:
    fake.fail_next(httpx.Response(307, headers={"location": f"{BASE_URL}/SAIB/filtered"}))
    page = client.list_documents("SAIB", filters={"drs:status": "Current"})
    assert page.documents
    first, second = fake.requests
    assert second.method == "POST"
    assert second.content == first.content


@pytest.mark.parametrize("page_size", [1, 7, 10, 24, 25, 750])
@pytest.mark.parametrize("offset", [0, 3, 24, 25, 40])
@pytest.mark.parametrize("sort", ["ASC", "DESC", None])
def test_pagination_returns_each_document_once(
    fake: FakeDRS, page_size: int, offset: int, sort: str | None
) -> None:
    fake.page_size = 750
    with DRSClient(API_KEY, http_client=httpx.Client(transport=fake.transport())) as drs:
        everything = [d.guid for d in drs.list_documents("BULK", sort=sort).documents]
        fake.page_size = page_size
        fake.requests.clear()
        guids = [d.guid for d in drs.iter_documents("BULK", offset=offset, sort=sort)]
    assert len(everything) == 25
    assert guids == everything[offset:]
    assert len(fake.requests) == max(1, -(-(25 - offset) // page_size))


def test_iter_pages_stops_on_empty_page_that_claims_more(client: DRSClient, fake: FakeDRS) -> None:
    summary = {
        "doctypeName": "SAIB",
        "drsDoctypeName": "SAIB",
        "count": 0,
        "hasMoreItems": True,
        "totalItems": 99,
        "offset": 0,
    }
    fake.fail_next(httpx.Response(200, json={"summary": summary, "documents": []}))
    assert len(list(client.iter_pages("SAIB"))) == 1
    assert len(fake.requests) == 1


def test_documents_can_be_deduplicated(client: DRSClient) -> None:
    docs = list(client.iter_documents("BULK")) + list(client.iter_documents("BULK", limit=5))
    assert len(set(docs)) == 25


def test_retry_log_never_contains_api_key(
    client: DRSClient, fake: FakeDRS, caplog: pytest.LogCaptureFixture
) -> None:
    fake.fail_next(httpx.Response(503, text="busy"), _raise(httpx.ConnectError("refused")))
    with caplog.at_level("WARNING", logger="faa_drs"):
        client.list_documents("SAIB")
    assert len(caplog.records) == 2
    assert all("Retry" in r.getMessage() for r in caplog.records)
    assert all(API_KEY not in r.getMessage() for r in caplog.records)


def test_download_to_new_directory_with_trailing_slash(client: DRSClient, tmp_path: Path) -> None:
    path = client.download_to("att-1", f"{tmp_path}/new/")
    assert path == tmp_path / "new" / "GUID.0001.att-1.pdf"


def test_download_to_ignores_dot_dot_file_name(
    client: DRSClient, fake: FakeDRS, tmp_path: Path
) -> None:
    fake.fail_next(
        httpx.Response(200, content=b"x", headers={"content-disposition": 'filename=".."'})
    )
    assert client.download_to("att-1", tmp_path) == tmp_path / "att-1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["att-1"]


def test_download_to_ignores_stale_part_file(client: DRSClient, tmp_path: Path) -> None:
    (tmp_path / ".out.pdf.part").write_bytes(b"stale")
    path = client.download_to("att-1", tmp_path / "out.pdf")
    assert path.read_bytes() == b"%PDF-1.7 attachment"


def test_download_rejects_non_string_source(client: DRSClient) -> None:
    not_a_source: Any = None
    with pytest.raises(TypeError):
        client.download(not_a_source)


def _crawl_while_updating(sort: str, *, already_read: bool) -> tuple[set[str], set[str]]:
    """Read 25 documents in pages of 10. After the first page, one document changes."""
    docs = [make_doc(i, modified=f"2024-01-01T00:00:{i:02d}.000Z") for i in range(25)]
    fake = FakeDRS(documents={"X": docs}, page_size=10)
    first_page = docs[:10] if sort == "ASC" else docs[15:]
    changed = first_page[3] if already_read else docs[12]
    seen: set[str] = set()
    with DRSClient(API_KEY, http_client=httpx.Client(transport=fake.transport())) as drs:
        for page in drs.iter_pages("X", sort=sort):
            seen.update(d.guid for d in page.documents)
            changed["docLastModifiedDate"] = "2025-01-01T00:00:00.000Z"
        newer = {d.guid for d in drs.iter_documents("X", modified_after="2024-01-01T00:00:24Z")}
    return {d["documentGuid"] for d in docs} - seen, newer


def test_asc_read_can_skip_an_unchanged_document_during_an_update() -> None:
    missed, newer = _crawl_while_updating("ASC", already_read=True)
    assert len(missed) == 1
    assert not missed & newer  # The skipped document did not change, so a sync cannot find it.


@pytest.mark.parametrize("already_read", [True, False])
def test_desc_read_plus_incremental_sync_misses_nothing(already_read: bool) -> None:
    missed, newer = _crawl_while_updating("DESC", already_read=already_read)
    assert missed <= newer
