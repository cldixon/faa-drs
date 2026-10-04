from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TypeVar

import anyio
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


class AsyncDRSClient:
    """Asynchronous client for the FAA DRS API.

    The methods and arguments are the same as `DRSClient`.

    Example:
        ```python
        from faa_drs import AsyncDRSClient, DocType

        async with AsyncDRSClient() as drs:
            async for doc in drs.iter_documents(DocType.SAIB, limit=10):
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
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = Settings.resolve(api_key, base_url, max_retries)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self._settings.base_url!r})"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_documents(
        self,
        doctype: str,
        *,
        offset: int = 0,
        modified_after: DateLike | None = None,
        sort: SortOrder | str | None = None,
        filters: Filters | None = None,
        keywords: Iterable[str] | str | None = None,
    ) -> Page:
        """Get one page of up to 750 documents. See `DRSClient.list_documents`."""
        query = build_query(
            doctype,
            offset=offset,
            modified_after=modified_after,
            sort=sort,
            filters=filters,
            keywords=keywords,
        )
        return await self._list(query)

    async def iter_pages(
        self,
        doctype: str,
        *,
        offset: int = 0,
        modified_after: DateLike | None = None,
        sort: SortOrder | str | None = SortOrder.DESC,
        filters: Filters | None = None,
        keywords: Iterable[str] | str | None = None,
    ) -> AsyncIterator[Page]:
        """Get all pages. See `DRSClient.iter_pages`."""
        query = build_query(
            doctype,
            offset=offset,
            modified_after=modified_after,
            sort=sort,
            filters=filters,
            keywords=keywords,
        )
        while True:
            page = await self._list(query)
            yield page
            if page.next_offset is None:
                return
            query = query.with_offset(page.next_offset)

    async def iter_documents(
        self,
        doctype: str,
        *,
        offset: int = 0,
        modified_after: DateLike | None = None,
        sort: SortOrder | str | None = SortOrder.DESC,
        filters: Filters | None = None,
        keywords: Iterable[str] | str | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Document]:
        """Get all matching documents. See `DRSClient.iter_documents`."""
        if limit is not None and limit <= 0:
            return
        count = 0
        async for page in self.iter_pages(
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

    async def get_attachments(self, source: Document | str) -> list[Attachment]:
        """Get the additional attachments of a document. See `DRSClient.get_attachments`."""
        if isinstance(source, Document) and not source.has_attachments:
            return []
        file_id = resolve_file_id(source)

        async def parse(response: httpx.Response) -> list[Attachment]:
            return parse_attachments(response)

        return await self._call(self._settings.attachments_request(file_id), parse)

    async def download(self, source: FileSource) -> bytes:
        """Download a file into memory. See `DRSClient.download`."""
        file_id = resolve_file_id(source)

        async def read(response: httpx.Response) -> bytes:
            check_status(response)
            return await response.aread()

        return await self._call(self._settings.download_request(file_id), read, stream=True)

    async def download_to(self, source: FileSource, dest: str | os.PathLike[str]) -> Path:
        """Stream a file to disk and return its path. See `DRSClient.download_to`."""
        file_id = resolve_file_id(source)

        async def write(response: httpx.Response) -> Path:
            check_status(response)
            target, part = download_target(dest, source, response, file_id)
            try:
                async with await anyio.open_file(part, "xb") as fh:
                    async for chunk in response.aiter_bytes(_CHUNK):
                        await fh.write(chunk)
                part.replace(target)
            finally:
                part.unlink(missing_ok=True)
            return target

        return await self._call(self._settings.download_request(file_id), write, stream=True)

    async def _list(self, query: Query) -> Page:
        async def parse(response: httpx.Response) -> Page:
            return parse_page(response)

        return await self._call(self._settings.list_request(query), parse)

    async def _call(
        self,
        request: httpx.Request,
        handle: Callable[[httpx.Response], Coroutine[Any, Any, T]],
        *,
        stream: bool = False,
    ) -> T:
        retry = self._settings.retry
        attempt = 0
        while True:
            response: httpx.Response | None = None
            try:
                response = await self._send(request, stream=stream)
                try:
                    if response.is_error:
                        await response.aread()
                    return await handle(response)
                finally:
                    await response.aclose()
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
            await anyio.sleep(delay)

    async def _send(self, request: httpx.Request, *, stream: bool) -> httpx.Response:
        response = await self._client.send(request, stream=stream, follow_redirects=False)
        for _ in range(MAX_REDIRECTS):
            if not response.has_redirect_location:
                return response
            await response.aclose()
            request = redirect_request(self._settings, response)
            response = await self._client.send(request, stream=stream, follow_redirects=False)
        if not response.has_redirect_location:
            return response
        await response.aclose()
        raise too_many_redirects(response)
