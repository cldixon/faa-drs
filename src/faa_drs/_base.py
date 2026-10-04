from __future__ import annotations

import logging
import math
import os
import random
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar
from urllib.parse import unquote

import httpx
from pydantic import BaseModel, ValidationError

from faa_drs._exceptions import (
    APIError,
    AuthenticationError,
    BadRequestError,
    DRSConnectionError,
    DRSError,
    DRSTimeoutError,
    NotFoundError,
    RateLimitError,
    ResponseValidationError,
    RestrictedDocTypeError,
    ServerError,
    UnknownDocTypeError,
)
from faa_drs._models import Attachment, Document, Page, file_id_from_url

if TYPE_CHECKING:
    from faa_drs._query import Query

logger = logging.getLogger("faa_drs")

API_KEY_ENV = "DRS_API_KEY"
DEFAULT_BASE_URL = "https://drs.faa.gov/api/drs/data-pull"
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DEFAULT_MAX_RETRIES = 3
PAGE_SIZE = 750

try:
    __version__ = version("faa-drs")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

USER_AGENT = f"faa-drs/{__version__} (+https://github.com/cldixon/faa-drs)"

M = TypeVar("M", bound=BaseModel)
FileSource: TypeAlias = Document | Attachment | str


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff: float = 0.5
    max_backoff: float = 30.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_backoff)
        ceiling = min(self.backoff * 2**attempt, self.max_backoff)
        return random.uniform(ceiling / 2, ceiling)  # noqa: S311


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str
    base_url: str
    retry: RetryPolicy

    @classmethod
    def resolve(cls, api_key: str | None, base_url: str, max_retries: int) -> Settings:
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        if not key or not key.strip():
            raise DRSError(
                f"No API key. Pass api_key=... or set the {API_KEY_ENV} environment variable."
            )
        if max_retries < 0:
            raise ValueError("max_retries must be 0 or more.")
        return cls(
            api_key=key.strip(),
            base_url=base_url.rstrip("/"),
            retry=RetryPolicy(max_retries=max_retries),
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "user-agent": USER_AGENT, "accept": "*/*"}

    def url(self, path: str) -> str:
        return self.base_url + path

    def list_request(self, query: Query) -> httpx.Request:
        method, path, kwargs = query.request_args()
        return httpx.Request(method, self.url(path), headers=self.headers, **kwargs)

    def attachments_request(self, file_id: str) -> httpx.Request:
        return httpx.Request(
            "GET", self.url(f"/get-other-attachment-details/{file_id}"), headers=self.headers
        )

    def download_request(self, file_id: str) -> httpx.Request:
        return httpx.Request("GET", self.url(f"/download/{file_id}"), headers=self.headers)


_FILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def resolve_file_id(source: FileSource) -> str:
    """Return the file id for a document, attachment, download URL or bare id.

    Only the id is used. The request always goes to the configured base URL, so the
    API key is never sent to a host taken from response data.
    """
    if isinstance(source, Document):
        if source.file_id is None:
            raise DRSError(f"Document {source.guid} has no downloadable file.")
        file_id = source.file_id
    elif isinstance(source, Attachment):
        file_id = source.file_id
    elif isinstance(source, str):
        file_id = file_id_from_url(source) if "/" in source else source.strip()
    else:
        raise TypeError(
            f"Expected a Document, Attachment, URL or file id. Got {type(source).__name__}."
        )
    if not _FILE_ID.fullmatch(file_id):
        raise DRSError(f"Invalid file id: {file_id!r}")
    return file_id


def default_file_name(source: FileSource, response: httpx.Response, file_id: str) -> str:
    if isinstance(source, (Document, Attachment)) and source.file_name:
        name = source.file_name
    else:
        name = _content_disposition_name(response) or file_id
    name = Path(name.replace("\\", "/")).name
    return name if name not in {"", ".", ".."} else file_id


def download_target(
    dest: str | os.PathLike[str], source: FileSource, response: httpx.Response, file_id: str
) -> tuple[Path, Path]:
    """Return `(target, part)` for a download and create the parent directory.

    `dest` is a directory if it exists as one or ends with a path separator. `part` is a
    unique temporary file next to the target, so concurrent downloads do not collide.
    """
    raw = os.fspath(dest)
    path = Path(raw)
    if path.is_dir() or raw.endswith(("/", os.sep)):
        path /= default_file_name(source, response, file_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path, path.with_name(f".{path.name}.{secrets.token_hex(4)}.part")


def _content_disposition_name(response: httpx.Response) -> str | None:
    header = response.headers.get("content-disposition")
    if not header:
        return None
    msg = Message()
    msg["content-disposition"] = header
    name = msg.get_filename()
    return unquote(name) if name else None


def map_transport_error(exc: httpx.TransportError) -> DRSConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return DRSTimeoutError(f"Request timed out: {exc}")
    return DRSConnectionError(f"Connection failed: {exc}")


_DOCTYPE_MISSING = re.compile(r"not present in DRS", re.IGNORECASE)
_DOCTYPE_INTERNAL = re.compile(r"internal only", re.IGNORECASE)
_SYSTEM_ERROR = re.compile(r"system error", re.IGNORECASE)


def error_for(response: httpx.Response, body: Any = None) -> APIError | None:  # noqa: PLR0911
    """Map a response to an exception, or `None` if the response is a success.

    DRS returns some errors as HTTP 200 with an `errorMessage` body, so the body is
    checked as well as the status code.
    """
    status = response.status_code
    message = body.get("errorMessage") if isinstance(body, dict) else None
    message = str(message) if message else None
    if status < 400 and not message:
        return None
    text = message or _error_text(response) or response.reason_phrase or "Request failed"
    if status in {401, 403}:
        return AuthenticationError(
            message or "Access denied. Check the API key.", status_code=status, response=response
        )
    if status == 429:
        return RateLimitError(text, status_code=status, response=response)
    if status >= 500 or _SYSTEM_ERROR.search(text):
        return ServerError(text, status_code=status, response=response)
    if _DOCTYPE_INTERNAL.search(text):
        return RestrictedDocTypeError(text, status_code=status, response=response)
    if _DOCTYPE_MISSING.search(text):
        return UnknownDocTypeError(text, status_code=status, response=response)
    if status == 404:
        return NotFoundError(text, status_code=status, response=response)
    if status == 400:
        return BadRequestError(text, status_code=status, response=response)
    return APIError(text, status_code=status, response=response)


def _error_text(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:500] or None
    except httpx.ResponseNotRead:
        return None
    if isinstance(body, dict):
        message = body.get("errorMessage") or body.get("message") or body.get("error")
        return str(message) if message else None
    return None


def check_status(response: httpx.Response) -> None:
    """Raise for an error status. The caller must read the body of an error response first."""
    if response.status_code >= 400:
        error = error_for(response)
        if error is not None:
            raise error


def parse_json(response: httpx.Response) -> Any:
    check_status(response)
    try:
        body = response.json()
    except ValueError as exc:
        raise ResponseValidationError(
            f"Expected JSON from {response.request.url.path}, "
            f"got {response.headers.get('content-type')}."
        ) from exc
    error = error_for(response, body)
    if error is not None:
        raise error
    return body


def validate(model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors[:3])
        more = f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""
        raise ResponseValidationError(
            f"Unexpected {model.__name__} response: {detail}{more}"
        ) from exc


def add_field_hint(error: BadRequestError, query: Query) -> None:
    """Name the filter fields that the catalog does not have. The API error does not."""
    if query.unknown_fields:
        error.message += (
            f" These filter fields are not in the catalog for {query.doctype}: "
            f"{', '.join(query.unknown_fields)}."
        )
        error.args = (error.message,)


def parse_page(response: httpx.Response) -> Page:
    return validate(Page, parse_json(response))


def parse_attachments(response: httpx.Response) -> list[Attachment]:
    body = parse_json(response)
    items = body.get("otherAttachmentDownloadDetails") if isinstance(body, dict) else None
    if not items:
        return []
    return [validate(Attachment, item) for item in items]


def retry_after(response: httpx.Response | None) -> float | None:
    """Return the `Retry-After` delay in seconds. The header is seconds or an HTTP date."""
    value = response.headers.get("retry-after") if response is not None else None
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return max(seconds, 0.0) if math.isfinite(seconds) else None


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, DRSConnectionError):
        return True
    return isinstance(exc, APIError) and exc.retryable


def log_retry(request: httpx.Request, exc: Exception, attempt: int, delay: float) -> None:
    logger.warning(
        "DRS %s %s failed (%s). Retry %d in %.1fs.",
        request.method,
        request.url.path,
        exc,
        attempt,
        delay,
    )


MAX_REDIRECTS = 5


def redirect_request(settings: Settings, response: httpx.Response) -> httpx.Request:
    """Build the next request for a redirect. The API key is kept only on the same origin.

    307 and 308 keep the method and body. Other redirects become a `GET` without a body.
    """
    request = response.request
    url = request.url.join(response.headers["location"])
    base = httpx.URL(settings.base_url)
    same_origin = (url.scheme, url.host, url.port) == (base.scheme, base.host, base.port)
    headers = settings.headers if same_origin else {"user-agent": USER_AGENT}
    if response.status_code in {307, 308} and request.method != "GET":
        content_type = request.headers.get("content-type")
        if content_type:
            headers = {**headers, "content-type": content_type}
        return httpx.Request(request.method, url, headers=headers, content=request.content)
    return httpx.Request("GET", url, headers=headers)


def too_many_redirects(response: httpx.Response) -> APIError:
    return APIError(
        f"Stopped after {MAX_REDIRECTS} redirects. Last location: "
        f"{response.headers.get('location')}",
        status_code=response.status_code,
        response=response,
    )
