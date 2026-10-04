"""Python SDK for the FAA Dynamic Regulatory System (DRS) API."""

from faa_drs import catalog
from faa_drs._async_client import AsyncDRSClient
from faa_drs._base import API_KEY_ENV, DEFAULT_BASE_URL, PAGE_SIZE, __version__
from faa_drs._client import DRSClient
from faa_drs._doctype import DocType
from faa_drs._exceptions import (
    APIError,
    AuthenticationError,
    BadRequestError,
    DRSConnectionError,
    DRSError,
    DRSTimeoutError,
    InvalidQueryError,
    NotFoundError,
    RateLimitError,
    ResponseValidationError,
    RestrictedDocTypeError,
    ServerError,
    UnknownDocTypeError,
    UnknownFieldWarning,
)
from faa_drs._models import Attachment, Document, Page, SortOrder

__all__ = [
    "API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "PAGE_SIZE",
    "APIError",
    "AsyncDRSClient",
    "Attachment",
    "AuthenticationError",
    "BadRequestError",
    "DRSClient",
    "DRSConnectionError",
    "DRSError",
    "DRSTimeoutError",
    "DocType",
    "Document",
    "InvalidQueryError",
    "NotFoundError",
    "Page",
    "RateLimitError",
    "ResponseValidationError",
    "RestrictedDocTypeError",
    "ServerError",
    "SortOrder",
    "UnknownDocTypeError",
    "UnknownFieldWarning",
    "__version__",
    "catalog",
]
