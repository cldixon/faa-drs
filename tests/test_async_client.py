from __future__ import annotations

import inspect
from pathlib import Path

import httpx
import pytest

from faa_drs import (
    APIError,
    AsyncDRSClient,
    BadRequestError,
    DocType,
    DRSClient,
    DRSConnectionError,
    DRSTimeoutError,
    RestrictedDocTypeError,
    ServerError,
)
from tests.fake_drs import API_KEY, BASE_URL, FakeDRS

pytestmark = pytest.mark.anyio


async def test_list_documents(aclient: AsyncDRSClient, fake: FakeDRS) -> None:
    page = await aclient.list_documents(DocType.SAIB)
    assert page.total == len(fake.documents["SAIB"])
    assert fake.requests[-1].headers["x-api-key"] == API_KEY


async def test_iter_documents(aclient: AsyncDRSClient, fake: FakeDRS) -> None:
    docs = [d async for d in aclient.iter_documents("BULK")]
    assert len({d.guid for d in docs}) == 25
    assert [r.url.params["offset"] for r in fake.requests] == ["0", "10", "20"]


async def test_iter_documents_limit(aclient: AsyncDRSClient, fake: FakeDRS) -> None:
    docs = [d async for d in aclient.iter_documents("BULK", limit=3)]
    assert len(docs) == 3
    assert len(fake.requests) == 1
    assert [d async for d in aclient.iter_documents("BULK", limit=0)] == []


async def test_iter_pages_filtered(aclient: AsyncDRSClient, fake: FakeDRS) -> None:
    pages = [p async for p in aclient.iter_pages("BULK", keywords="DOC-0000")]
    assert sum(len(p.documents) for p in pages) == 10
    assert fake.requests[0].method == "POST"


async def test_errors(aclient: AsyncDRSClient) -> None:
    with pytest.raises(RestrictedDocTypeError):
        await aclient.list_documents("ICAO_ANNEX")


async def test_retry(aclient: AsyncDRSClient, fake: FakeDRS, sleeps: list[float]) -> None:
    fake.fail_next(httpx.Response(502), httpx.Response(429, headers={"retry-after": "2"}))
    assert (await aclient.list_documents("SAIB")).documents
    assert sleeps == [0.01, 2.0]


async def test_retry_exhausted(fake: FakeDRS, sleeps: list[float]) -> None:
    fake.fail_next(httpx.Response(500), httpx.Response(500))
    http = httpx.AsyncClient(transport=fake.async_transport())
    async with AsyncDRSClient(API_KEY, http_client=http, max_retries=1) as drs:
        with pytest.raises(ServerError):
            await drs.list_documents("SAIB")
    assert not http.is_closed
    await http.aclose()


async def test_connection_error_retry(aclient: AsyncDRSClient, fake: FakeDRS) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fake.fail_next(refuse)
    assert (await aclient.list_documents("SAIB")).documents


async def test_attachments_and_downloads(
    aclient: AsyncDRSClient, fake: FakeDRS, tmp_path: Path
) -> None:
    doc = await anext(aclient.iter_documents("BULK", sort=None, offset=1, limit=1))
    assert await aclient.get_attachments(doc) == []
    attachments = await aclient.get_attachments(doc.model_copy(update={"has_attachments": True}))
    assert attachments[0].download_url == f"{BASE_URL}/download/att-1"
    assert await aclient.download(doc) == b"%PDF-1.7 main"
    path = await aclient.download_to(attachments[0], tmp_path)
    assert path == tmp_path / "Appendix A.pdf"
    assert path.read_bytes() == b"%PDF-1.7 attachment"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Appendix A.pdf"]


async def test_redirect_drops_key(aclient: AsyncDRSClient, fake: FakeDRS) -> None:
    fake.fail_next(
        httpx.Response(301, headers={"location": "https://cdn.example.com/f"}),
        httpx.Response(200, content=b"x"),
    )
    assert await aclient.download("att-1") == b"x"
    assert "x-api-key" not in fake.requests[-1].headers


async def test_owned_client_closes() -> None:
    drs = AsyncDRSClient("key")
    await drs.close()
    assert drs._client.is_closed
    assert repr(drs).startswith("AsyncDRSClient(")


def test_sync_and_async_clients_have_the_same_api() -> None:
    def public(cls: type) -> dict[str, inspect.Signature]:
        return {
            name: inspect.signature(member)
            for name, member in vars(cls).items()
            if callable(member) and not name.startswith("_")
        }

    sync, async_ = public(DRSClient), public(AsyncDRSClient)
    assert set(sync) == set(async_)
    for name, signature in sync.items():
        assert list(signature.parameters) == list(async_[name].parameters), name
        for param, value in signature.parameters.items():
            assert value.default == async_[name].parameters[param].default, (name, param)
    init_sync, init_async = inspect.signature(DRSClient), inspect.signature(AsyncDRSClient)
    assert list(init_sync.parameters) == list(init_async.parameters)


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (httpx.ConnectError("refused"), DRSConnectionError),
        (httpx.ReadTimeout("slow"), DRSTimeoutError),
    ],
)
async def test_transport_errors_are_wrapped(
    fake: FakeDRS, sleeps: list[float], exc: Exception, error: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    fake.fail_next(handler)
    async with AsyncDRSClient(
        API_KEY, http_client=httpx.AsyncClient(transport=fake.async_transport()), max_retries=0
    ) as drs:
        with pytest.raises(error) as info:
            await drs.list_documents("SAIB")
    assert info.value.__cause__ is exc


async def test_client_errors_are_not_retried(
    aclient: AsyncDRSClient, fake: FakeDRS, sleeps: list[float]
) -> None:
    fake.fail_next(httpx.Response(400, json={"errorMessage": "One or more filters are invalid"}))
    with pytest.raises(BadRequestError):
        await aclient.list_documents("SAIB")
    assert sleeps == []


async def test_download_to_leaves_no_partial_file(
    aclient: AsyncDRSClient, fake: FakeDRS, tmp_path: Path
) -> None:
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            raise httpx.ReadError("dropped")

    fake.fail_next(*[lambda r: httpx.Response(200, stream=Broken()) for _ in range(4)])
    with pytest.raises(DRSConnectionError):
        await aclient.download_to("att-1", tmp_path / "out.pdf")
    assert list(tmp_path.iterdir()) == []


async def test_download_to_directory_with_trailing_slash(
    aclient: AsyncDRSClient, tmp_path: Path
) -> None:
    path = await aclient.download_to("att-1", f"{tmp_path}/new/")
    assert path == tmp_path / "new" / "GUID.0001.att-1.pdf"
    assert path.read_bytes() == b"%PDF-1.7 attachment"


async def test_too_many_redirects(aclient: AsyncDRSClient, fake: FakeDRS) -> None:
    loop = httpx.Response(302, headers={"location": f"{BASE_URL}/download/loop"})
    fake.fail_next(*[loop] * 10)
    with pytest.raises(APIError, match="redirects"):
        await aclient.download("att-1")
    assert len(fake.requests) == 6


async def test_filtered_query_survives_307(aclient: AsyncDRSClient, fake: FakeDRS) -> None:
    fake.fail_next(httpx.Response(307, headers={"location": f"{BASE_URL}/SAIB/filtered"}))
    assert (await aclient.list_documents("SAIB", keywords="Boeing")).documents is not None
    assert fake.requests[1].method == "POST"
    assert fake.requests[1].content == fake.requests[0].content
