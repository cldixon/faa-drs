# faa-drs

[![CI](https://github.com/cldixon/faa-drs/actions/workflows/ci.yml/badge.svg)](https://github.com/cldixon/faa-drs/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/faa-drs)](https://pypi.org/project/faa-drs/)
[![Python](https://img.shields.io/pypi/pyversions/faa-drs)](https://pypi.org/project/faa-drs/)

Python SDK for the FAA [Dynamic Regulatory System](https://drs.faa.gov) (DRS) API.

Documentation: <https://cldixon.github.io/faa-drs/>

## Install

```sh
uv add faa-drs
```

## Set the API key

Get a key from the DRS API page in DRS Help & Training. Then set the key in the environment:

```sh
export DRS_API_KEY="your-key"
```

## Use

```python
from faa_drs import DRSClient, DocType

with DRSClient() as drs:
    for doc in drs.iter_documents(DocType.AC, filters={"drs:status": "Current"}):
        print(doc.number, doc.title)
        drs.download_to(doc, "downloads/")
```

The client:

- Gets all pages for you. The API returns a maximum of 750 documents per page.
- Validates filters against the document type before it sends a request.
- Retries connection errors, timeouts, HTTP 429 and HTTP 5xx.
- Returns typed models. `doc.content` contains the full text when the API supplies it.

An async client, `AsyncDRSClient`, has the same methods.

## Develop

```sh
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run pytest
uv run pytest -m live   # Calls the real API. Needs DRS_API_KEY.
uv run zensical serve   # Docs at http://localhost:8000
```

## License

MIT. This project is not affiliated with the FAA.
