from __future__ import annotations

import difflib
import warnings
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any, TypeAlias
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from faa_drs import catalog
from faa_drs._exceptions import InvalidQueryError, UnknownFieldWarning
from faa_drs._models import SortOrder

MAX_FILTERS = 5
MAX_FILTER_VALUES = 10
KEYWORD_FILTER = "Keyword"

DateLike: TypeAlias = date | datetime | str
FilterValue: TypeAlias = str | DateLike | Iterable[str] | tuple[DateLike, DateLike]
Filters: TypeAlias = Mapping[str, FilterValue]


class Query(BaseModel):
    """A validated request for one page of documents."""

    model_config = ConfigDict(frozen=True)

    doctype: str = Field(min_length=1)
    offset: int = Field(default=0, ge=0)
    modified_after: datetime | None = None
    sort: SortOrder | None = None
    filters: dict[str, list[str]] = Field(default_factory=dict)
    unknown_fields: tuple[str, ...] = ()
    """Filter fields that are not in the catalog for the document type."""

    @field_validator("doctype", mode="before")
    @classmethod
    def _doctype(cls, value: Any) -> Any:
        return str(value).strip() if value is not None else value

    @field_validator("sort", mode="before")
    @classmethod
    def _sort(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("modified_after", mode="before")
    @classmethod
    def _modified_after(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=UTC)
        return value

    @field_validator("modified_after")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        try:
            return value.astimezone(UTC)
        except OverflowError as exc:
            raise ValueError("datetime is out of range in UTC") from exc

    @property
    def is_filtered(self) -> bool:
        return bool(self.filters)

    @property
    def modified_after_param(self) -> str | None:
        if self.modified_after is None:
            return None
        stamp = self.modified_after.replace(tzinfo=None).isoformat(timespec="milliseconds")
        return stamp + "Z"

    def with_offset(self, offset: int) -> Query:
        return self.model_copy(update={"offset": offset})

    def request_args(self) -> tuple[str, str, dict[str, Any]]:
        """Return `(method, path, kwargs)` for `httpx.Client.request`."""
        path = "/" + quote(self.doctype, safe="")
        if self.is_filtered:
            body: dict[str, Any] = {"offset": self.offset, "documentFilters": self.filters}
            if self.modified_after is not None:
                body["docLastModifiedDate"] = self.modified_after_param
            if self.sort is not None:
                body["sortOrder"] = self.sort.value
            return "POST", path + "/filtered", {"json": body}
        params: dict[str, str | int] = {"offset": self.offset}
        if self.modified_after is not None:
            params["docLastModifiedDate"] = self.modified_after_param or ""
        if self.sort is not None:
            params["docLastModifiedDateSortOrder"] = self.sort.value
        return "GET", path, {"params": params}


def build_query(
    doctype: str,
    *,
    offset: int = 0,
    modified_after: DateLike | None = None,
    sort: SortOrder | str | None = None,
    filters: Filters | None = None,
    keywords: Iterable[str] | str | None = None,
) -> Query:
    doctype = str(doctype).strip() if doctype is not None else ""
    info = catalog.find_doctype(doctype)
    normalized, unknown = _normalize_filters(filters or {}, info)
    words = _strings([keywords] if isinstance(keywords, str) else keywords or [], KEYWORD_FILTER)
    if words:
        normalized[KEYWORD_FILTER] = _dedupe(normalized.get(KEYWORD_FILTER, []) + words)
    if len(normalized) > MAX_FILTERS:
        raise InvalidQueryError(
            f"The API accepts at most {MAX_FILTERS} filters, keywords included. "
            f"Got {len(normalized)}: {', '.join(normalized)}."
        )
    for key, values in normalized.items():
        if len(values) > MAX_FILTER_VALUES:
            raise InvalidQueryError(
                f"Filter {key!r} has {len(values)} values. The API accepts at most "
                f"{MAX_FILTER_VALUES}."
            )
    try:
        return Query(
            doctype=doctype,
            offset=offset,
            modified_after=modified_after,  # type: ignore[arg-type]
            sort=sort,  # type: ignore[arg-type]
            filters=normalized,
            unknown_fields=unknown,
        )
    except ValidationError as exc:
        raise InvalidQueryError(_first_error(exc)) from exc


def _normalize_filters(
    filters: Filters, info: catalog.DocTypeInfo | None
) -> tuple[dict[str, list[str]], tuple[str, ...]]:
    """Return the normalized filters and the fields that the catalog does not have."""
    result: dict[str, list[str]] = {}
    unknown: list[str] = []
    for key, raw in filters.items():
        # Read a generator once.
        value = (
            list(raw) if isinstance(raw, Iterable) and not isinstance(raw, (str, Mapping)) else raw
        )
        field = None
        if info is not None and key != KEYWORD_FILTER:
            field = info.fields.get(key)
            if field is None:
                unknown.append(key)
                warnings.warn(_unknown_field_message(key, info), UnknownFieldWarning, stacklevel=4)
        if (field is not None and field.type is catalog.FieldType.DATE) or (
            field is None and _looks_like_date_range(value)
        ):
            values = _date_range(key, value)
        else:
            if isinstance(value, (date, datetime)):
                raise InvalidQueryError(f"Filter {key!r} is not a date field.")
            values = _strings([value] if isinstance(value, str) else value, key)
        if values:
            result[key] = values
    return result, tuple(unknown)


def _looks_like_date_range(value: Any) -> bool:
    """True for a pair that holds a `date`, for a field whose type the catalog does not know."""
    return isinstance(value, list) and any(isinstance(item, date) for item in value)


def _strings(values: Any, key: str) -> list[str]:
    if not isinstance(values, Iterable):
        raise InvalidQueryError(f"Filter {key!r} must be a string or a list of strings.")
    out: list[str] = []
    for item in values:
        if item is None:
            continue
        if not isinstance(item, str):
            raise InvalidQueryError(
                f"Filter {key!r} values must be strings. Got {type(item).__name__}."
            )
        if item.strip():
            out.append(item.strip())
    return _dedupe(out)


def _date_range(key: str, value: Any) -> list[str]:
    if isinstance(value, (str, date)) or not isinstance(value, Iterable):
        raise InvalidQueryError(
            f"Date filter {key!r} must be a (start, end) pair, for example "
            f"(date(2020, 1, 1), date(2025, 12, 31))."
        )
    items = list(value)
    if len(items) != 2:
        raise InvalidQueryError(f"Date filter {key!r} must have exactly 2 values: start, end.")
    start, end = (_to_date(key, item) for item in items)
    if start > end:
        raise InvalidQueryError(f"Date filter {key!r}: start {start} is after end {end}.")
    return [start.isoformat(), end.isoformat()]


def _to_date(key: str, value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise InvalidQueryError(f"Date filter {key!r}: {value!r} is not a YYYY-MM-DD date.")


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _unknown_field_message(key: str, info: catalog.DocTypeInfo) -> str:
    message = (
        f"{key!r} is not in the catalog for document type {info.code!r}. The filter is sent "
        "anyway. The API rejects fields that it does not know."
    )
    close = difflib.get_close_matches(key, list(info.fields), n=3, cutoff=0.6)
    if close:
        message += f" Did you mean: {', '.join(close)}?"
    return message + f" See catalog.get_doctype({info.code!r}).fields."


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(part) for part in err["loc"]) or "query"
    return f"Invalid {loc}: {err['msg']}"
