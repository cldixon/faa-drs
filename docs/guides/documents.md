# Get documents

## Get one page

`list_documents` sends one request. It returns a `Page`.

```python
page = drs.list_documents(DocType.SAIB, offset=750)

page.total  # Number of documents that match.
page.offset  # Offset of this page.
page.documents  # Up to 750 documents.
page.has_more  # True if there are more pages.
page.next_offset  # Offset of the next page, or None.
```

## Get all documents

`iter_documents` gets each page when you need it:

```python
for doc in drs.iter_documents(DocType.SAIB):
    ...
```

Use `limit` to stop after a number of documents:

```python
docs = list(drs.iter_documents(DocType.SAIB, limit=50))
```

Use `iter_pages` to get full pages:

```python
for page in drs.iter_pages(DocType.STC):
    save(page.documents)
```

## Sort order

The API can sort by last-modified date only.

| `sort` | Order |
| --- | --- |
| `"ASC"` | Oldest change first. Documents with no date come first. |
| `"DESC"` | Newest change first. Documents with no date come last. |
| `None` | The default order of the document type. |

`list_documents` uses `None` by default. `iter_documents` and `iter_pages` use `"DESC"` by default.

Paging uses offsets. If DRS changes documents during a long read, the positions move:

- With `"ASC"`, a changed document moves to the end, and the read gets it again. But if the read already got that document, each later document moves back one position, and the read can skip a document that did not change.
- With `"DESC"`, a changed document moves to the start. The read can get a document twice. It does not skip a document that did not change. It can miss the changed document, but a later read with `modified_after` gets it.

This is why `iter_documents` and `iter_pages` use `"DESC"`. DRS updates once every 24 hours, so this occurs only if a read continues across an update. Replace documents by `guid`, because a read can get a document twice. See [Sync updates](sync.md).

To use the order of the DRS web application, set `sort=None`.

## Resume a read

Each page has an offset. To continue after an interruption, start at the last offset that you completed:

```python
for page in drs.iter_pages(DocType.PMA, offset=checkpoint):
    save(page.documents)
    checkpoint = page.next_offset
```

For a better method, see [Sync updates](sync.md).

## Large document types

Some document types are very large. PMA has more than 1.6 million documents, or about 2,200 pages. ADFRAWD pages can be 20 MB each because they contain full text.

Use `iter_pages` and save each page before you request the next.
