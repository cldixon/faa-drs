"""Document types and their metadata fields.

Source: the FAA "DRS Document Types Metadata Mapping" spreadsheet, corrected against the
live API.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib.resources import files
from types import MappingProxyType

__all__ = ["DocTypeInfo", "FieldInfo", "FieldType", "find_doctype", "get_doctype", "list_doctypes"]


class FieldType(StrEnum):
    TEXT = "TEXT"
    """A string or `None`."""
    ARRAY = "ARRAY"
    """A list of strings, or `None`."""
    DATE = "DATE"
    """A `YYYY-MM-DD` string, or `None`."""


@dataclass(frozen=True, slots=True)
class FieldInfo:
    key: str
    """API name, for example `drs:title`."""
    label: str
    """Display name in the DRS web application."""
    type: FieldType


@dataclass(frozen=True, slots=True)
class DocTypeInfo:
    code: str
    """API code, for example `AC`."""
    name: str
    """Display name in the DRS web application."""
    service: str
    """FAA service or office, for example `AIR` or `FS`."""
    sort_field: str | None
    """Field the API sorts by when no sort order is given."""
    internal_only: bool
    """External API keys cannot read this type."""
    fields: Mapping[str, FieldInfo]
    """Metadata fields, keyed by API name."""
    content_fields: tuple[str, ...]
    """Fields that carry the full document text inline."""

    @property
    def has_inline_content(self) -> bool:
        return bool(self.content_fields)

    @property
    def date_fields(self) -> tuple[str, ...]:
        return tuple(k for k, f in self.fields.items() if f.type is FieldType.DATE)


@cache
def _registry() -> Mapping[str, DocTypeInfo]:
    raw = json.loads(files("faa_drs").joinpath("catalog.json").read_text(encoding="utf-8"))
    registry: dict[str, DocTypeInfo] = {}
    for item in raw["doctypes"]:
        fields = {
            f["key"]: FieldInfo(key=f["key"], label=f["label"], type=FieldType(f["type"]))
            for f in item["fields"]
        }
        registry[item["code"]] = DocTypeInfo(
            code=item["code"],
            name=item["name"],
            service=item["service"],
            sort_field=item["sort_field"],
            internal_only=item["internal_only"],
            fields=MappingProxyType(fields),
            content_fields=tuple(item["content_fields"]),
        )
    return MappingProxyType(registry)


def find_doctype(code: str) -> DocTypeInfo | None:
    """Return the document type for a code, or `None` if the catalog does not have it."""
    return _registry().get(str(code))


def get_doctype(code: str) -> DocTypeInfo:
    """Return the document type for a code.

    Raises:
        KeyError: The catalog does not have the code.
    """
    info = find_doctype(code)
    if info is None:
        message = f"Unknown document type {code!r}."
        close = difflib.get_close_matches(str(code).upper(), list(_registry()), n=3)
        if close:
            message += f" Did you mean: {', '.join(close)}?"
        raise KeyError(message)
    return info


def list_doctypes(
    *, service: str | None = None, include_internal: bool = False
) -> list[DocTypeInfo]:
    """Return document types, sorted by code.

    Args:
        service: Return only this service, for example `AIR`.
        include_internal: Include types that external API keys cannot read.
    """
    return [
        info
        for info in _registry().values()
        if (include_internal or not info.internal_only)
        and (service is None or info.service == service.upper())
    ]
