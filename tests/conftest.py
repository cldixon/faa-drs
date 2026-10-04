from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest

from faa_drs import AsyncDRSClient, DRSClient
from faa_drs._base import RetryPolicy
from tests.fake_drs import API_KEY, BASE_URL, FakeDRS, make_doc

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def real_docs() -> dict[str, list[dict]]:
    """Trimmed documents captured from the live API."""
    return json.loads((FIXTURES / "documents.json").read_text())["documents"]


@pytest.fixture
def fake(real_docs: dict[str, list[dict]]) -> FakeDRS:
    return FakeDRS(
        documents={
            **real_docs,
            "BULK": [
                make_doc(i, modified=f"2024-01-{1 + i % 28:02d}T00:00:{i % 60:02d}.000Z")
                for i in range(25)
            ],
        },
        files={"file-00001": b"%PDF-1.7 main", "att-1": b"%PDF-1.7 attachment"},
        attachments={
            "file-00001": [
                {"fileDownloadURL": f"{BASE_URL}/download/att-1", "fileName": "Appendix A.pdf"}
            ]
        },
        page_size=10,
    )


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record retry delays without sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr("faa_drs._client.time.sleep", recorded.append)

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr("faa_drs._async_client.anyio.sleep", fake_sleep)
    monkeypatch.setattr(
        RetryPolicy,
        "delay",
        lambda self, attempt, retry_after=None: (
            retry_after if retry_after is not None else 0.01 * 2**attempt
        ),
    )
    return recorded


@pytest.fixture
def client(fake: FakeDRS, sleeps: list[float]) -> Iterator[DRSClient]:
    with DRSClient(API_KEY, http_client=httpx.Client(transport=fake.transport())) as drs:
        yield drs


@pytest.fixture
async def aclient(fake: FakeDRS, sleeps: list[float]) -> AsyncIterator[AsyncDRSClient]:
    http = httpx.AsyncClient(transport=fake.async_transport())
    async with AsyncDRSClient(API_KEY, http_client=http) as drs:
        yield drs
    await http.aclose()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
