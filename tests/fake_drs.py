"""In-memory fake of the DRS API for `httpx.MockTransport`.

Reproduces the observed behavior of the real API, including its quirks:
errors in HTTP 200 bodies, 403 with an empty body, fixed page size, strict
`docLastModifiedDate` comparison and nulls first on ascending sort.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import unquote

import httpx

BASE_URL = "https://drs.faa.gov/api/drs/data-pull"
API_KEY = "test-key"

Handler = Callable[[httpx.Request], httpx.Response]


def make_doc(i: int, *, modified: str | None = "2024-01-01T00:00:00.000Z", **metadata: Any) -> dict:
    file_id = f"file-{i:05d}"
    return {
        "drs:documentNumber": f"DOC-{i:05d}",
        "drs:title": f"Document {i}",
        "drs:status": "Current",
        **metadata,
        "docLastModifiedDate": modified,
        "documentGuid": f"GUID{i:05d}",
        "documentURL": f"https://drs.faa.gov/browse/excelExternalWindow/GUID{i:05d}.0001",
        "mainDocumentDownloadURL": f"{BASE_URL}/download/{file_id}",
        "mainDocumentFileName": f"DOC-{i:05d}.pdf",
        "hasMoreAttachments": False,
    }


@dataclass
class FakeDRS:
    documents: dict[str, list[dict]] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    attachments: dict[str, list[dict]] = field(default_factory=dict)
    internal: set[str] = field(default_factory=lambda: {"ICAO_ANNEX"})
    page_size: int = 750
    api_key: str = API_KEY
    requests: list[httpx.Request] = field(default_factory=list)
    script: deque[Handler | httpx.Response] = field(default_factory=deque)

    def fail_next(self, *responses: Handler | httpx.Response) -> None:
        """Queue responses returned before normal handling resumes."""
        self.script.extend(responses)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def async_transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        self.requests.append(request)
        if self.script:
            item = self.script.popleft()
            return item if isinstance(item, httpx.Response) else item(request)
        if request.headers.get("x-api-key") != self.api_key:
            return httpx.Response(403)
        path = unquote(request.url.path.removeprefix("/api/drs/data-pull/"))
        if path.startswith("download/"):
            return self._download(path.removeprefix("download/"))
        if path.startswith("get-other-attachment-details/"):
            return self._attachments(path.removeprefix("get-other-attachment-details/"))
        if request.method == "POST" and path.endswith("/filtered"):
            return self._list(path.removesuffix("/filtered"), json.loads(request.content), True)
        if request.method == "GET" and "/" not in path:
            params = request.url.params
            body = {
                "offset": int(params.get("offset", 0)),
                "docLastModifiedDate": params.get("docLastModifiedDate"),
                "sortOrder": params.get("docLastModifiedDateSortOrder"),
            }
            return self._list(path, body, False)
        return httpx.Response(404, json={"error": "Not Found"})

    def _list(self, doctype: str, body: dict, filtered: bool) -> httpx.Response:
        if doctype in self.internal:
            return _error(
                f"Could not process the request. The doctype {doctype} is internal only doctype"
            )
        if doctype not in self.documents:
            return _error(f"The doctype {doctype} is not present in DRS")
        docs = list(self.documents[doctype])
        if since := body.get("docLastModifiedDate"):
            docs = [
                d for d in docs if d["docLastModifiedDate"] and d["docLastModifiedDate"] > since
            ]
        if filtered:
            filters = body.get("documentFilters") or {}
            if len(filters) > 5 or any(len(v) > 10 for v in filters.values()):
                return httpx.Response(
                    400, json={"errorMessage": "One or more filters provided are invalid"}
                )
            docs = [d for d in docs if _matches(d, filters)]
        sort_order = body.get("sortOrder")
        if sort_order:
            docs.sort(key=lambda d: d["docLastModifiedDate"] or "", reverse=sort_order == "DESC")
        offset = int(body.get("offset") or 0)
        page = docs[offset : offset + self.page_size]
        return httpx.Response(
            200,
            json={
                "summary": {
                    "doctypeName": doctype,
                    "drsDoctypeName": f"{doctype} (fake)",
                    "count": len(page),
                    "hasMoreItems": offset + len(page) < len(docs),
                    "totalItems": len(docs),
                    "offset": offset,
                    "sortBy": "docLastModifiedDate" if sort_order else "drs:documentNumber",
                    "sortByOrder": sort_order or "ASC",
                },
                "documents": page,
            },
        )

    def _download(self, file_id: str) -> httpx.Response:
        if file_id not in self.files:
            return httpx.Response(404, json={"error": "Not Found"})
        return httpx.Response(
            200,
            content=self.files[file_id],
            headers={
                "content-type": "application/pdf",
                "content-disposition": f'attachment; filename="GUID.0001.{file_id}.pdf"',
            },
        )

    def _attachments(self, file_id: str) -> httpx.Response:
        items = self.attachments.get(file_id)
        if not items:
            return httpx.Response(
                200, json={"comments": "No additional attachments or all are restricted."}
            )
        return httpx.Response(200, json={"otherAttachmentDownloadDetails": items})


def _error(message: str) -> httpx.Response:
    return httpx.Response(200, json={"errorMessage": message})


def _matches(doc: dict, filters: dict[str, list[str]]) -> bool:
    for key, values in filters.items():
        if key == "Keyword":
            text = json.dumps(doc).lower()
            if not any(v.lower() in text for v in values):
                return False
            continue
        actual = doc.get(key)
        if _is_date_range(values) and isinstance(actual, str) and _is_date(actual):
            if not values[0] <= actual[:10] <= values[1]:
                return False
            continue
        actual_values = actual if isinstance(actual, list) else [actual]
        if not set(values) & {v for v in actual_values if v is not None}:
            return False
    return True


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value[:10])
    except ValueError:
        return False
    return True


def _is_date_range(values: list[str]) -> bool:
    return len(values) == 2 and all(_is_date(v) for v in values)
