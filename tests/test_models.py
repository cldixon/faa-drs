from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from faa_drs import Attachment, Document, Page, SortOrder


def doc(real_docs: dict[str, list[dict]], doctype: str, i: int = 0) -> Document:
    return Document.model_validate({**real_docs[doctype][i], "doctype": doctype})


def test_common_fields_are_typed(real_docs: dict[str, list[dict]]) -> None:
    d = doc(real_docs, "SAIB")
    assert d.doctype == "SAIB"
    assert d.guid.startswith("DRSDOCID")
    assert isinstance(d.last_modified, datetime)
    assert d.last_modified.tzinfo is not None
    assert d.download_url is not None
    assert d.file_id == d.download_url.rsplit("/", 1)[-1]
    assert d.file_name is not None
    assert d.file_name.endswith(".pdf")


def test_metadata_holds_doctype_fields_only(real_docs: dict[str, list[dict]]) -> None:
    d = doc(real_docs, "SAIB")
    assert "drs:documentNumber" in d
    assert "documentGuid" not in d.metadata
    assert d.number == d["drs:documentNumber"]
    assert d.status in {"Current", "Historical"}
    assert d.get("drs:missing", "fallback") == "fallback"


def test_empty_sentinel_becomes_none_and_keeps_position(real_docs: dict[str, list[dict]]) -> None:
    raw = real_docs["AC"][0]
    assert "_EMPTY_" in raw["drs:subPart"]
    d = doc(real_docs, "AC")
    sub = d["drs:subPart"]
    assert isinstance(sub, list)
    assert None in sub
    assert "_EMPTY_" not in sub
    assert len(sub) == len(raw["drs:subPart"])
    assert len(d.get_list("drs:subPart")) == sum(1 for v in raw["drs:subPart"] if v != "_EMPTY_")


def test_null_last_modified_is_allowed(real_docs: dict[str, list[dict]]) -> None:
    d = doc(real_docs, "SAIB", -1)
    assert d.last_modified is None


def test_document_without_file(real_docs: dict[str, list[dict]]) -> None:
    d = doc(real_docs, "PMA")
    assert d.download_url is None
    assert d.file_id is None
    assert d.has_attachments is False


def test_guid_is_required() -> None:
    with pytest.raises(ValidationError):
        Document.model_validate({"doctype": "AC", "documentURL": "x"})


def test_get_date(real_docs: dict[str, list[dict]]) -> None:
    d = doc(real_docs, "SAIB")
    assert isinstance(d.get_date("drs:saibIssueDate"), date)
    assert d.get_date("drs:comments") is None
    assert d.get_date("drs:missing") is None


def test_get_list_wraps_scalars(real_docs: dict[str, list[dict]]) -> None:
    d = doc(real_docs, "SAIB")
    assert d.get_list("drs:documentNumber") == [d.number]
    assert d.get_list("drs:missing") == []


@pytest.mark.parametrize(
    ("doctype", "field"),
    [
        ("FAR", "drs:farSectionRule"),
        ("ADFRAWD", "drs:adfrawdRegulatoryText"),
        ("NPRM", "drs:nprmSupplementaryInfo"),
    ],
)
def test_inline_content(real_docs: dict[str, list[dict]], doctype: str, field: str) -> None:
    d = doc(real_docs, doctype)
    assert field in d.content
    assert all(isinstance(text, str) and text for text in d.content.values())


def test_no_inline_content_for_file_only_types(real_docs: dict[str, list[dict]]) -> None:
    assert doc(real_docs, "AC").content == {}
    unknown = Document.model_validate({"doctype": "NEW_TYPE", "documentGuid": "G"})
    assert unknown.content == {}


def test_documents_are_immutable(real_docs: dict[str, list[dict]]) -> None:
    d: Any = doc(real_docs, "SAIB")
    with pytest.raises(ValidationError):
        d.guid = "other"


def test_round_trip_by_alias(real_docs: dict[str, list[dict]]) -> None:
    d = doc(real_docs, "SAIB")
    again = Document.model_validate(d.model_dump())
    assert again == d


def test_page_flattens_summary(real_docs: dict[str, list[dict]]) -> None:
    page = Page.model_validate(
        {
            "summary": {
                "doctypeName": "SAIB",
                "drsDoctypeName": "Special Airworthiness Information Bulletins (SAIB)",
                "count": 2,
                "hasMoreItems": True,
                "totalItems": 1338,
                "offset": 750,
                "sortBy": "drs:saibIssueDate",
                "sortByOrder": "desc",
            },
            "documents": real_docs["SAIB"][:2],
        }
    )
    assert page.doctype == "SAIB"
    assert page.total == 1338
    assert page.sort_order is SortOrder.DESC
    assert page.next_offset == 752
    assert all(d.doctype == "SAIB" for d in page.documents)


def test_last_page_has_no_next_offset() -> None:
    page = Page.model_validate(
        {
            "summary": {
                "doctypeName": "X",
                "drsDoctypeName": "X",
                "count": 0,
                "hasMoreItems": False,
                "totalItems": 3,
                "offset": 900,
            },
            "documents": [],
        }
    )
    assert page.next_offset is None
    assert page.sort_order is None


def test_attachment_file_id() -> None:
    a = Attachment.model_validate(
        {
            "fileDownloadURL": "https://drs.faa.gov/api/drs/data-pull/download/abc-123",
            "fileName": "A.pdf",
        }
    )
    assert a.file_id == "abc-123"


def test_last_modified_parses_utc(real_docs: dict[str, list[dict]]) -> None:
    d = doc(real_docs, "SAIB")
    assert d.last_modified is not None
    assert d.last_modified.utcoffset() == datetime(2020, 1, 1, tzinfo=UTC).utcoffset()


def test_documents_hash_by_identity(real_docs: dict[str, list[dict]]) -> None:
    a, b = doc(real_docs, "SAIB"), doc(real_docs, "SAIB")
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b, doc(real_docs, "SAIB", 1)}) == 2


def test_document_accepts_field_names() -> None:
    d = Document.model_validate({"doctype": "AC", "guid": "G", "drs:title": "T"})
    assert d.guid == "G"
    assert d.title == "T"
    assert "guid" not in d.metadata
    assert Document(doctype="AC", guid="G").metadata == {}


@pytest.mark.parametrize(("raw", "expected"), [("asc", SortOrder.ASC), ("NONE", None), (7, None)])
def test_page_tolerates_unknown_sort_order(raw: Any, expected: SortOrder | None) -> None:
    page = Page.model_validate(
        {
            "summary": {
                "doctypeName": "X",
                "drsDoctypeName": "X",
                "count": 0,
                "hasMoreItems": False,
                "totalItems": 0,
                "offset": 0,
                "sortByOrder": raw,
            },
            "documents": [],
        }
    )
    assert page.sort_order == expected


def test_metadata_mapping_access(real_docs: dict[str, list[dict]]) -> None:
    d = doc(real_docs, "SAIB")
    with pytest.raises(KeyError):
        d["drs:missing"]
    assert "drs:missing" not in d
    assert d.get("drs:missing") is None
