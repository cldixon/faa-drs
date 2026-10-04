from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

__all__ = [
    "APIError",
    "AuthenticationError",
    "BadRequestError",
    "DRSConnectionError",
    "DRSError",
    "DRSTimeoutError",
    "InvalidQueryError",
    "NotFoundError",
    "RateLimitError",
    "ResponseValidationError",
    "RestrictedDocTypeError",
    "ServerError",
    "UnknownDocTypeError",
    "UnknownFieldWarning",
]


class DRSError(Exception):
    """Base class for all errors raised by this package."""


class UnknownFieldWarning(UserWarning):
    """A filter field is not in the catalog for its document type.

    The client sends the filter anyway, because the catalog can be behind the API. If the
    API does not know the field either, the request fails with `BadRequestError`.
    """


class InvalidQueryError(DRSError, ValueError):
    """The query fails local validation. No request was sent."""


class DRSConnectionError(DRSError):
    """The client cannot reach the DRS API."""


class DRSTimeoutError(DRSConnectionError):
    """The request exceeded the configured timeout."""


class ResponseValidationError(DRSError):
    """The API response does not have the expected shape."""


class APIError(DRSError):
    """The DRS API rejected the request or returned an error message."""

    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response = response

    def __str__(self) -> str:
        return f"[{self.status_code}] {self.message}"

    def __reduce__(self) -> tuple[Any, ...]:
        # The response is not kept, so errors can cross process boundaries.
        return _rebuild, (type(self), self.message, self.status_code)


def _rebuild(cls: type[APIError], message: str, status_code: int) -> APIError:
    return cls(message, status_code=status_code)


class BadRequestError(APIError):
    """HTTP 400. Usually an invalid filter for the document type."""


class AuthenticationError(APIError):
    """HTTP 403. The API key is missing, invalid or expired."""


class NotFoundError(APIError):
    """HTTP 404. The file or resource does not exist."""


class UnknownDocTypeError(APIError):
    """The document type is not present in DRS."""


class RestrictedDocTypeError(APIError):
    """The document type is internal only. External API keys cannot read it."""


class RateLimitError(APIError):
    """HTTP 429."""

    retryable = True


class ServerError(APIError):
    """HTTP 5xx, or a DRS system error message."""

    retryable = True
