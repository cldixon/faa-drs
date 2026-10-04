"""Unit tests for the transport-independent helpers shared by both clients."""

from __future__ import annotations

import pickle
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from faa_drs import (
    APIError,
    Attachment,
    AuthenticationError,
    BadRequestError,
    Document,
    DRSConnectionError,
    DRSError,
    DRSTimeoutError,
    NotFoundError,
    RateLimitError,
    RestrictedDocTypeError,
    ServerError,
    UnknownDocTypeError,
)
from faa_drs._base import (
    USER_AGENT,
    RetryPolicy,
    Settings,
    default_file_name,
    download_target,
    error_for,
    is_retryable,
    redirect_request,
    resolve_file_id,
    retry_after,
)
from tests.fake_drs import API_KEY, BASE_URL, make_doc

SETTINGS = Settings.resolve(API_KEY, BASE_URL, 3)


def _response(status: int = 200, **kwargs: Any) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", f"{BASE_URL}/X"), **kwargs)


# Retry-After


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("7", 7.0),
        ("1.5", 1.5),
        ("-3", 0.0),
        ("nan", None),
        ("inf", None),
        ("soon", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0),
    ],
)
def test_retry_after(header: str | None, expected: float | None) -> None:
    headers = {"retry-after": header} if header is not None else {}
    assert retry_after(_response(429, headers=headers)) == expected


def test_retry_after_future_http_date() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)
    delay = retry_after(_response(429, headers={"retry-after": when}))
    assert delay is not None
    assert 50 < delay <= 60


def test_retry_after_without_response() -> None:
    assert retry_after(None) is None


# Backoff


@given(attempt=st.integers(0, 20))
def test_backoff_is_jittered_and_capped(attempt: int) -> None:
    policy = RetryPolicy(backoff=0.5, max_backoff=30.0)
    ceiling = min(0.5 * 2**attempt, 30.0)
    assert ceiling / 2 <= policy.delay(attempt) <= ceiling


def test_retry_after_is_capped_by_max_backoff() -> None:
    policy = RetryPolicy(max_backoff=30.0)
    assert policy.delay(0, retry_after=3600) == 30.0
    assert policy.delay(5, retry_after=2) == 2


# Error mapping


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, None, AuthenticationError),
        (403, None, AuthenticationError),
        (429, None, RateLimitError),
        (500, None, ServerError),
        (502, None, ServerError),
        (504, None, ServerError),
        (400, {"errorMessage": "One or more filters provided are invalid"}, BadRequestError),
        (404, {"error": "Not Found"}, NotFoundError),
        (418, None, APIError),
        (200, {"errorMessage": "The doctype X is not present in DRS"}, UnknownDocTypeError),
        (
            200,
            {"errorMessage": "Could not process the request. The doctype X is internal only"},
            RestrictedDocTypeError,
        ),
        (200, {"errorMessage": "Unable to retrieve documents due to system error"}, ServerError),
        (200, {"errorMessage": "Something new"}, APIError),
        (200, {"errorMessage": {"code": 7}}, APIError),
    ],
)
def test_error_for(status: int, body: object, error: type[APIError]) -> None:
    response = _response(status, json=body) if body is not None else _response(status)
    result = error_for(response, body)
    assert type(result) is error
    assert result.status_code == status
    assert result.response is response


@pytest.mark.parametrize("body", [None, {}, {"errorMessage": None}, {"errorMessage": ""}, []])
def test_success_is_not_an_error(body: object) -> None:
    assert error_for(_response(200), body) is None


def test_error_message_prefers_body() -> None:
    error = error_for(_response(400, json={"message": "bad field"}))
    assert error is not None
    assert error.message == "bad field"
    assert str(error) == "[400] bad field"
    html = error_for(_response(502, text="<html>gateway</html>"))
    assert html is not None
    assert html.message == "<html>gateway</html>"


def test_retryable_errors() -> None:
    assert is_retryable(ServerError("x", status_code=500))
    assert is_retryable(RateLimitError("x", status_code=429))
    assert is_retryable(DRSTimeoutError("x"))
    assert not is_retryable(BadRequestError("x", status_code=400))
    assert not is_retryable(AuthenticationError("x", status_code=403))
    assert not is_retryable(DRSError("x"))


# Exceptions


@pytest.mark.parametrize(
    "cls",
    [
        APIError,
        AuthenticationError,
        BadRequestError,
        NotFoundError,
        RateLimitError,
        RestrictedDocTypeError,
        ServerError,
        UnknownDocTypeError,
    ],
)
def test_api_errors_pickle(cls: type[APIError]) -> None:
    error = cls("boom", status_code=599, response=_response(599))
    again = pickle.loads(pickle.dumps(error))  # noqa: S301
    assert type(again) is cls
    assert (again.message, again.status_code, again.response) == ("boom", 599, None)
    assert str(again) == str(error)


def test_exception_hierarchy() -> None:
    assert issubclass(DRSTimeoutError, DRSConnectionError)
    assert issubclass(DRSConnectionError, DRSError)
    assert issubclass(UnknownDocTypeError, APIError)
    assert issubclass(APIError, DRSError)


# File ids


def test_resolve_file_id_sources() -> None:
    doc = Document.model_validate({**make_doc(3), "doctype": "X"})
    attachment = Attachment(file_name="a.pdf", download_url=f"{BASE_URL}/download/att-9/")
    assert resolve_file_id(doc) == "file-00003"
    assert resolve_file_id(attachment) == "att-9"
    assert resolve_file_id(f"{BASE_URL}/download/abc?x=1#frag") == "abc"
    assert resolve_file_id("  abc.123_x-y  ") == "abc.123_x-y"


def test_resolve_file_id_rejects_other_types() -> None:
    not_a_source: Any = 5
    with pytest.raises(TypeError, match="int"):
        resolve_file_id(not_a_source)


@given(st.text())
def test_resolved_file_ids_are_one_safe_path_segment(source: str) -> None:
    try:
        file_id = resolve_file_id(source)
    except DRSError:
        return
    assert file_id
    assert file_id[0] not in ".-_"
    assert not set(file_id) & set("/\\?#%: \t\r\n")


# File names


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        ('attachment; filename="report.pdf"', "report.pdf"),
        ('attachment; filename="../../etc/passwd"', "passwd"),
        ('attachment; filename="..\\\\..\\\\win.ini"', "win.ini"),
        ('attachment; filename=".."', "fid"),
        ('attachment; filename="."', "fid"),
        ("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf", "résumé.pdf"),
        ("attachment", "fid"),
        (None, "fid"),
    ],
)
def test_default_file_name(disposition: str | None, expected: str) -> None:
    headers = {"content-disposition": disposition} if disposition else {}
    assert default_file_name("fid", _response(headers=headers), "fid") == expected


@given(st.text())
def test_default_file_name_is_one_path_component(name: str) -> None:
    attachment = Attachment(file_name=name or "x", download_url=f"{BASE_URL}/download/fid")
    result = default_file_name(attachment, _response(), "fid")
    assert result not in {"", ".", ".."}
    assert "/" not in result
    assert "\\" not in result


def test_download_target(tmp_path: Path) -> None:
    response = _response()
    attachment = Attachment(file_name="a.pdf", download_url=f"{BASE_URL}/download/fid")
    target, part = download_target(tmp_path, attachment, response, "fid")
    assert target == tmp_path / "a.pdf"
    assert part.parent == tmp_path
    assert part.name.startswith(".a.pdf.")
    assert part.name.endswith(".part")
    _, other = download_target(tmp_path, attachment, response, "fid")
    assert other != part

    target, _ = download_target(f"{tmp_path}/new/", attachment, response, "fid")
    assert target == tmp_path / "new" / "a.pdf"
    assert target.parent.is_dir()

    target, _ = download_target(tmp_path / "x" / "named.bin", attachment, response, "fid")
    assert target == tmp_path / "x" / "named.bin"


# Redirects


def _redirect(status: int, location: str, request: httpx.Request) -> httpx.Response:
    return httpx.Response(status, headers={"location": location}, request=request)


@pytest.mark.parametrize("status", [307, 308])
def test_redirect_keeps_post_body_for_307_308(status: int) -> None:
    original = httpx.Request(
        "POST", f"{BASE_URL}/X/filtered", headers=SETTINGS.headers, json={"offset": 0}
    )
    request = redirect_request(SETTINGS, _redirect(status, "/api/drs/v2/X/filtered", original))
    assert request.method == "POST"
    assert request.content == original.content
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-api-key"] == API_KEY
    assert request.url == "https://drs.faa.gov/api/drs/v2/X/filtered"


@pytest.mark.parametrize("status", [301, 302, 303])
def test_redirect_becomes_get_for_other_codes(status: int) -> None:
    original = httpx.Request("POST", f"{BASE_URL}/X/filtered", json={"offset": 0})
    request = redirect_request(SETTINGS, _redirect(status, f"{BASE_URL}/X", original))
    assert request.method == "GET"
    assert request.content == b""


@pytest.mark.parametrize(
    "location",
    [
        "https://files.example.com/x",
        "http://drs.faa.gov/api/drs/data-pull/download/x",
        "https://drs.faa.gov:8443/x",
        "https://drs.faa.gov.example.com/x",
    ],
)
def test_redirect_drops_key_off_origin(location: str) -> None:
    original = httpx.Request("GET", f"{BASE_URL}/download/x", headers=SETTINGS.headers)
    request = redirect_request(SETTINGS, _redirect(302, location, original))
    assert "x-api-key" not in request.headers
    assert request.headers["user-agent"] == USER_AGENT
