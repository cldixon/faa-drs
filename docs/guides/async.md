# Async client

`AsyncDRSClient` has the same methods as `DRSClient`. Use `await` and `async for`.

```python
import asyncio

from faa_drs import AsyncDRSClient, DocType


async def main():
    async with AsyncDRSClient() as drs:
        page = await drs.list_documents(DocType.AC)
        async for doc in drs.iter_documents(DocType.SAIB, limit=10):
            print(doc.number)


asyncio.run(main())
```

## Download files at the same time

Use a semaphore to limit the number of requests at the same time:

```python
import asyncio


async def download_all(drs, docs, dest, limit=4):
    semaphore = asyncio.Semaphore(limit)

    async def one(doc):
        async with semaphore:
            return await drs.download_to(doc, dest)

    return await asyncio.gather(*(one(d) for d in docs if d.download_url))
```

!!! warning
    DRS is a public FAA service. Keep the number of requests at the same time low. Four is a good maximum.
