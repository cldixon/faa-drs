from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from types import TracebackType
from typing import Self, TypeVar

import httpx

from faa_drs._base import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    MAX_REDIRECTS,
    FileSource,
    Settings,
    check_status,
    download_target,
    is_retryable,
    log_retry,
    map_transport_error,
    parse_attachments,
    parse_page,
    redirect_request,
    resolve_file_id,
    retry_after,
    too_many_redirects,
)
from faa_drs._exceptions import APIError, DRSConnectionError
from faa_drs._models import Attachment, Document, Page, SortOrder
from faa_drs._query import DateLike, Filters, Query, build_query

T = TypeVar("T")
_CHUNK = 1 << 16


class DRSClient:
    """Synchronous client for the FAA DRS API.

    Args:
        api_key: DRS API key. Defaults to the `DRS_API_KEY` environment variable.
        base_url: API root. Change only for testing.
        timeout: Seconds, or an `httpx.Timeout`.
        max_retries: Retries for connection errors, timeouts, HTTP 429 and 5xx.
        http_client: Your own `httpx.Client`. The client does not close it.

    Example:
        ```python
        from faa_drs import DRSClient, DocType

        with DRSClient() as drs:
            for doc in drs.iter_documents(DocType.SAIB, limit=10):
                print(doc.number, doc.title)
        ```
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = Settings.resolve(api_key, base_url, max_retries)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self._settings.base_url!r})"

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def list_documents(
        self,
        doctype: str,
        *,
        offset: int = 0,
        modified_after: DateLike | None = None,
        sort: SortOrder | str | None = None,
        filters: Filters | None = None,
        keywords: Iterable[str] | str | None = None,
    ) -> Page:
        """Get one page of up to 750 documents.

        Without `filters` or `keywords` this calls `GET /{doctype}`. With them it calls
        `POST /{doctype}/filtered`.

        Args:
            doctype: Document type code, for example `DocType.AC` or `"AC"`.
            offset: Number of documents to skip.
            modified_after: Return only documents modified after this time (UTC).
            sort: Sort by last-modified date. `None` uses the API default sort.
            filters: Metadata filters, for example `{"drs:status": ["Current"]}`.
                Date fields take a `(start, end)` pair.
            keywords: Full-text search terms. A document matches if it has any term.
        """
        query = build_query(
            doctype,
            offset=offset,
            modified_after=modified_after,
            sort=sort,
            filters=filters,
            keywords=keywords,
        )
        return self._list(query)

    def iter_pages(
        self,
        doctype: str,
        *,
        offset: int = 0,
        modified_after: DateLike | None = None,
        sort: SortOrder | str | None = SortOrder.DESC,
        filters: Filters | None = None,
        keywords: Iterable[str] | str | None = None,
    ) -> Iterator[Page]:
        """Get all pages, one request per page.

        The default sort is newest-modified first, and documents with no date come last.
        Paging uses offsets. With this order, an update to DRS during a long read can
        repeat a document but does not skip one that did not change. A document that
        changes during the read can be missed, but it is newer than the first document,
        so a later read with `modified_after` gets it. Use `sort=None` for the API
        default order.
        """
        query = build_query(
            doctype,
            offset=offset,
            modified_after=modified_after,
            sort=sort,
            filters=filters,
            keywords=keywords,
        )
        while True:
            page = self._list(query)
            yield page
            if page.next_offset is None:
                return
            query = query.with_offset(page.next_offset)

    def iter_documents(
        self,
        doctype: str,
        *,
        offset: int = 0,
        modified_after: DateLike | None = None,
        sort: SortOrder | str | None = SortOrder.DESC,
        filters: Filters | None = None,
        keywords: Iterable[str] | str | None = None,
        limit: int | None = None,
    ) -> Iterator[Document]:
        """Get all matching documents. Pages are requested as needed.

        Args:
            limit: Stop after this many documents.

        Other arguments are the same as `iter_pages`.
        """
        if limit is not None and limit <= 0:
            return
        count = 0
        for page in self.iter_pages(
            doctype,
            offset=offset,
            modified_after=modified_after,
            sort=sort,
            filters=filters,
            keywords=keywords,
        ):
            for document in page.documents:
                yield document
                count += 1
                if limit is not None and count >= limit:
                    return

    def get_attachments(self, source: Document | str) -> list[Attachment]:
        """Get the additional attachments of a document.

        Args:
            source: A `Document`, or the file id or download URL of its main file.
        """
        if isinstance(source, Document) and not source.has_attachments:
            return []
        file_id = resolve_file_id(source)
        return self._call(self._settings.attachments_request(file_id), parse_attachments)

    def download(self, source: FileSource) -> bytes:
        """Download a file into memory.

        Args:
            source: A `Document` (its main file), an `Attachment`, a download URL or a file id.
        """
        file_id = resolve_file_id(source)

        def read(response: httpx.Response) -> bytes:
            check_status(response)
            return response.read()

        return self._call(self._settings.download_request(file_id), read, stream=True)

    def download_to(self, source: FileSource, dest: str | os.PathLike[str]) -> Path:
        """Stream a file to disk and return its path.

        If `dest` is an existing directory, or ends with `/`, the file is saved in it. The
        file name comes from the document or attachment, else from the response. The write
        is atomic. A partial file is never left at the destination.
        """
        file_id = resolve_file_id(source)

        def write(response: httpx.Response) -> Path:
            check_status(response)
            target, part = download_target(dest, source, response, file_id)
            try:
                with part.open("xb") as fh:
                    for chunk in response.iter_bytes(_CHUNK):
                        fh.write(chunk)
                part.replace(target)
            finally:
                part.unlink(missing_ok=True)
            return target

        return self._call(self._settings.download_request(file_id), write, stream=True)

    def _list(self, query: Query) -> Page:
        return self._call(self._settings.list_request(query), parse_page)

    def _call(
        self,
        request: httpx.Request,
        handle: Callable[[httpx.Response], T],
        *,
        stream: bool = False,
    ) -> T:
        retry = self._settings.retry
        attempt = 0
        while True:
            response: httpx.Response | None = None
            try:
                response = self._send(request, stream=stream)
                try:
                    if response.is_error:
                        response.read()
                    return handle(response)
                finally:
                    response.close()
            except httpx.TransportError as exc:
                error: Exception = map_transport_error(exc)
                error.__cause__ = exc
            except (APIError, DRSConnectionError) as exc:
                error = exc
            if attempt >= retry.max_retries or not is_retryable(error):
                raise error
            attempt += 1
            delay = retry.delay(attempt - 1, retry_after(response))
            log_retry(request, error, attempt, delay)
            time.sleep(delay)

    def _send(self, request: httpx.Request, *, stream: bool) -> httpx.Response:
        response = self._client.send(request, stream=stream, follow_redirects=False)
        for _ in range(MAX_REDIRECTS):
            if not response.has_redirect_location:
                return response
            response.close()
            request = redirect_request(self._settings, response)
            response = self._client.send(request, stream=stream, follow_redirects=False)
        if not response.has_redirect_location:
            return response
        response.close()
        raise too_many_redirects(response)
