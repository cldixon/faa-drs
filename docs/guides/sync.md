# Sync updates

Use this procedure to keep a local copy of a document type up to date.

## Procedure

1. Do a full read. Save each document and the latest `last_modified` value.
2. At a later time, read with `modified_after` set to the saved value.
3. Save the new and changed documents. Replace documents that have the same `guid`.
4. Save the new latest `last_modified` value.
5. Do steps 2 to 4 again.

```python
from faa_drs import DRSClient, DocType


def sync(drs: DRSClient, store, checkpoint=None):
    latest = checkpoint
    for doc in drs.iter_documents(DocType.SAIB, modified_after=checkpoint):
        store.upsert(doc.guid, doc.model_dump_json())
        if doc.last_modified and (latest is None or doc.last_modified > latest):
            latest = doc.last_modified
    return latest
```

## Rules

- `modified_after` returns documents that changed **after** the time. It does not include the time.
- `modified_after` does not return documents that have no `last_modified` value. A full read gets these documents.
- `iter_documents` sorts oldest change first. If the read stops, you can continue from the last `last_modified` value that you saved.
- DRS updates every 24 hours. Do not sync more frequently than this.

!!! warning
    Many documents have no `last_modified` value. For example, 585 of 1,338 SAIBs. Do a full read again from time to time to find changes to these documents.
