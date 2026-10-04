# Errors and retries

## Errors

All errors are subclasses of `DRSError`.

| Error | Cause |
| --- | --- |
| `InvalidQueryError` | The query is not valid. The client did not send a request. Also a `ValueError`. |
| `AuthenticationError` | HTTP 401 or 403. The API key is missing, not valid or expired. |
| `UnknownDocTypeError` | The document type is not in DRS. |
| `RestrictedDocTypeError` | The document type is internal only. External keys cannot read it. |
| `BadRequestError` | HTTP 400. Usually a filter that is not valid. The message names filter fields that are not in the catalog. |
| `NotFoundError` | HTTP 404. Usually a file that does not exist. |
| `RateLimitError` | HTTP 429. |
| `ServerError` | HTTP 5xx, or a DRS system error. |
| `DRSConnectionError` | The client cannot connect to DRS. |
| `DRSTimeoutError` | The request took too long. A subclass of `DRSConnectionError`. |
| `ResponseValidationError` | The response does not have the expected shape. |

`UnknownFieldWarning` is a warning, not an error. The client gives it for a filter field that is not in the catalog, and sends the filter anyway. See [Filters](filters.md#find-field-names).

Errors from the API are `APIError` subclasses. They have `status_code`, `message` and `response` attributes. You can pickle them, for example to send them from a worker process. A pickled error does not keep `response`.

`APIError` is the base class for errors from the API. It has these attributes:

- `status_code`: The HTTP status code.
- `message`: The error message from the API.
- `response`: The `httpx.Response`.

!!! note
    DRS sends some errors with HTTP status 200. The client finds these errors in the response body. For example, `UnknownDocTypeError` has `status_code` 200.

```python
from faa_drs import DRSError, RestrictedDocTypeError

try:
    drs.list_documents("ICAO_ANNEX")
except RestrictedDocTypeError:
    print("Internal only.")
except DRSError as error:
    print(f"Failed: {error}")
```

## Retries

The client retries these errors:

- `DRSConnectionError` and `DRSTimeoutError`
- `RateLimitError`
- `ServerError`

The client waits longer before each retry. If the response has a `Retry-After` header, the client waits for that time, to a maximum of 30 seconds. The header can be a number of seconds or a date.

Set the number of retries with `max_retries`. The default is 3. Set it to 0 to stop retries.

```python
drs = DRSClient(max_retries=5, timeout=120)
```

## Logs

The client writes a warning to the `faa_drs` logger before each retry. The log does not contain the API key.

```python
import logging

logging.basicConfig(level=logging.INFO)
```
