from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from faa_drs import AsyncDRSClient, DocType, RestrictedDocTypeError, ServerError
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
