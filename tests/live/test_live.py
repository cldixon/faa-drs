"""Contract tests against the real DRS API.

Run with `uv run pytest -m live`. Needs `DRS_API_KEY`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date

import pytest

from faa_drs import (
    API_KEY_ENV,
    AsyncDRSClient,
    AuthenticationError,
    DocType,
    DRSClient,
    RestrictedDocTypeError,
    UnknownDocTypeError,
    catalog,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get(API_KEY_ENV), reason=f"{API_KEY_ENV} is not set"),
]

COMMON = {
    "docLastModifiedDate",
    "documentGuid",
    "documentURL",
    "mainDocumentDownloadURL",
    "mainDocumentFileName",
    "hasMoreAttachments",
    "otherAttachmentDetailsUrl",
}


@pytest.fixture(scope="module")
def drs() -> Iterator[DRSClient]:
    with DRSClient() as client:
        yield client


def test_list_and_paginate(drs: DRSClient) -> None:
    page = drs.list_documents(DocType.ALERTS)
    assert page.total == page.count == len(page.documents)
    assert page.has_more is False
    docs = list(drs.iter_documents(DocType.ALERTS))
    assert len({d.guid for d in docs}) == page.total


def test_page_size_is_750(drs: DRSClient) -> None:
    page = drs.list_documents(DocType.SAIB)
    assert page.total > 750
    assert len(page.documents) == 750
    assert page.next_offset == 750


@pytest.mark.parametrize("doctype", [DocType.AC, DocType.SAIB, DocType.ADFRAWD, DocType.FAR])
def test_catalog_matches_live_fields(drs: DRSClient, doctype: DocType) -> None:
    page = drs.list_documents(doctype)
    live = {k for d in page.documents for k in d.metadata}
    assert live == set(catalog.get_doctype(doctype).fields), (
        "Catalog drift. Update spec/doctypes.csv."
    )
    raw_keys = set(page.documents[0].model_dump(by_alias=True)) - {"doctype", "metadata"}
    assert raw_keys <= COMMON


def test_inline_content(drs: DRSClient) -> None:
    doc = next(drs.iter_documents(DocType.FAR, limit=1))
    assert doc.content["drs:farSectionRule"]


def test_filters_and_keywords(drs: DRSClient) -> None:
    page = drs.list_documents(
        DocType.SAIB,
        filters={
            "drs:status": "Current",
            "drs:saibIssueDate": (date(2020, 1, 1), date(2025, 12, 31)),
        },
        keywords=["corrosion"],
    )
    assert page.documents
    for doc in page.documents:
        assert doc.status == "Current"
        issued = doc.get_date("drs:saibIssueDate")
        assert issued is not None
        assert date(2020, 1, 1) <= issued <= date(2025, 12, 31)


def test_modified_after_is_strict(drs: DRSClient) -> None:
    newest = drs.list_documents(DocType.SAIB, sort="desc").documents[0]
    assert newest.last_modified is not None
    assert drs.list_documents(DocType.SAIB, modified_after=newest.last_modified).total == 0


def test_download_and_attachments(drs: DRSClient) -> None:
    doc = next(d for d in drs.list_documents(DocType.AC).documents if d.has_attachments)
    assert drs.download(doc)[:5] == b"%PDF-"
    attachments = drs.get_attachments(doc)
    assert attachments
    assert drs.download(attachments[0])


def test_error_quirks(drs: DRSClient) -> None:
    with pytest.raises(UnknownDocTypeError):
        drs.list_documents("NOT_A_DOCTYPE")
    with pytest.raises(RestrictedDocTypeError):
        drs.list_documents(DocType.ICAO_ANNEX)
    with DRSClient("invalid-key") as bad, pytest.raises(AuthenticationError):
        bad.list_documents(DocType.AC)


@pytest.mark.anyio
async def test_async_client() -> None:
    async with AsyncDRSClient() as drs:
        docs = [d async for d in drs.iter_documents(DocType.ALERTS, limit=5)]
    assert len(docs) == 5
