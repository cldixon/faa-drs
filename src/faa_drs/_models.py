from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from faa_drs import catalog

__all__ = ["Attachment", "Document", "Page", "SortOrder"]

EMPTY_SENTINEL = "_EMPTY_"
_COMMON_KEYS = {
    "docLastModifiedDate",
    "documentGuid",
    "documentURL",
    "mainDocumentDownloadURL",
    "mainDocumentFileName",
    "hasMoreAttachments",
    "otherAttachmentDetailsUrl",
}


class SortOrder(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


def file_id_from_url(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1]


def _normalize(value: Any) -> Any:
    if value == EMPTY_SENTINEL:
        return None
    if isinstance(value, list):
        return [None if item == EMPTY_SENTINEL else item for item in value]
    return value


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")


class Attachment(_Model):
    """An additional file attached to a document."""

    file_name: str = Field(alias="fileName")
    download_url: str = Field(alias="fileDownloadURL")

    @property
    def file_id(self) -> str:
        return file_id_from_url(self.download_url)


_DOCUMENT_KEYS = _COMMON_KEYS | {
    "doctype",
    "guid",
    "url",
    "last_modified",
    "download_url",
    "file_name",
    "has_attachments",
    "attachments_url",
}


class Document(_Model):
    """One DRS document.

    Common fields are typed attributes. Only `guid` is always present. Fields specific to
    the document type are in `metadata`, keyed by their API name (for example
    `drs:title`). The value `_EMPTY_` from the API is converted to `None`.
    """

    doctype: str
    guid: str = Field(alias="documentGuid")
    url: str | None = Field(default=None, alias="documentURL")
    last_modified: datetime | None = Field(default=None, alias="docLastModifiedDate")
    download_url: str | None = Field(default=None, alias="mainDocumentDownloadURL")
    file_name: str | None = Field(default=None, alias="mainDocumentFileName")
    has_attachments: bool = Field(default=False, alias="hasMoreAttachments")
    attachments_url: str | None = Field(default=None, alias="otherAttachmentDetailsUrl")
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _split_metadata(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "metadata" in data:
            return data
        common = {k: v for k, v in data.items() if k in _DOCUMENT_KEYS}
        common["metadata"] = {k: _normalize(v) for k, v in data.items() if k not in common}
        return common

    def __hash__(self) -> int:
        return hash((self.doctype, self.guid))

    def __getitem__(self, key: str) -> JsonValue:
        return self.metadata[key]

    def __contains__(self, key: object) -> bool:
        return key in self.metadata

    def get(self, key: str, default: JsonValue = None) -> JsonValue:
        return self.metadata.get(key, default)

    def get_list(self, key: str) -> list[str]:
        """Return a metadata value as a list of strings. `None` items are dropped."""
        value = self.metadata.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        return [str(value)]

    def get_date(self, key: str) -> date | None:
        """Return a `YYYY-MM-DD` metadata value as a `date`."""
        value = self.metadata.get(key)
        if not isinstance(value, str) or not value:
            return None
        return date.fromisoformat(value[:10])

    def _text(self, key: str) -> str | None:
        value = self.metadata.get(key)
        return value if isinstance(value, str) else None

    @property
    def number(self) -> str | None:
        return self._text("drs:documentNumber")

    @property
    def title(self) -> str | None:
        return self._text("drs:title")

    @property
    def status(self) -> str | None:
        return self._text("drs:status")

    @property
    def file_id(self) -> str | None:
        return file_id_from_url(self.download_url) if self.download_url else None

    @property
    def content(self) -> dict[str, str]:
        """Full-text fields returned inline by the API, keyed by field name.

        Empty for document types that publish their content only as a file. List values
        are joined with blank lines.
        """
        info = catalog.find_doctype(self.doctype)
        if info is None:
            return {}
        result: dict[str, str] = {}
        for key in info.content_fields:
            text = "\n\n".join(self.get_list(key)).strip()
            if text:
                result[key] = text
        return result


class Page(_Model):
    """One page of results. The API returns at most 750 documents per page."""

    doctype: str = Field(alias="doctypeName")
    doctype_name: str = Field(alias="drsDoctypeName")
    count: int
    total: int = Field(alias="totalItems")
    offset: int
    has_more: bool = Field(alias="hasMoreItems")
    sort_by: str | None = Field(default=None, alias="sortBy")
    sort_order: SortOrder | None = Field(default=None, alias="sortByOrder")
    documents: list[Document]

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "summary" not in data:
            return data
        summary = data["summary"]
        if not isinstance(summary, dict):
            return data
        doctype = summary.get("doctypeName")
        documents = [
            {**doc, "doctype": doctype} if isinstance(doc, dict) else doc
            for doc in data.get("documents") or []
        ]
        return {**summary, "documents": documents}

    @field_validator("sort_order", mode="before")
    @classmethod
    def _sort_order(cls, value: Any) -> Any:
        # Informational only. An unknown value must not fail the whole page.
        if isinstance(value, str) and value.upper() in SortOrder.__members__:
            return value.upper()
        return None

    @property
    def next_offset(self) -> int | None:
        """Offset of the next page, or `None` if this is the last page."""
        if not self.has_more or not self.documents:
            return None
        return self.offset + len(self.documents)
