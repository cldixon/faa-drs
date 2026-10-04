# faa-drs

Python SDK for the FAA [Dynamic Regulatory System](https://drs.faa.gov) (DRS) API.

DRS contains FAA regulatory and guidance documents. Examples are Advisory Circulars, Airworthiness Directives, SAIBs, Orders and 14 CFR. The FAA updates DRS every 24 hours.

```python
from faa_drs import DRSClient, DocType

with DRSClient() as drs:
    for doc in drs.iter_documents(DocType.SAIB, keywords="corrosion"):
        print(doc.number, doc.title)
```

## Features

- **All pages.** The client gets all pages for you. The API returns a maximum of 750 documents per page.
- **Document type codes.** `DocType` contains all 105 codes. Your editor completes them for you.
- **Local validation.** The client validates filters against the document type before it sends a request.
- **Typed models.** Documents are [Pydantic](https://docs.pydantic.dev) models. Common fields are attributes.
- **Full text.** `doc.content` contains the full text when the API supplies it.
- **Safe downloads.** The client streams files to disk. A failed download does not leave a partial file.
- **Retries.** The client retries connection errors, timeouts, HTTP 429 and HTTP 5xx.
- **Sync and async.** `DRSClient` and `AsyncDRSClient` have the same methods.

## Next steps

1. [Get started](get-started.md). Install the package and make your first request.
2. Read the [guides](guides/documents.md).
3. Find a document type in the [document type list](reference/doctypes.md).

!!! note
    This project is not affiliated with the FAA. Do not use it as an authoritative source of regulatory data. Always confirm against [drs.faa.gov](https://drs.faa.gov).
