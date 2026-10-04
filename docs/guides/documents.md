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
| `"DESC"` | Newest change first. |
| `None` | The default order of the document type. |

`list_documents` uses `None` by default. `iter_documents` and `iter_pages` use `"ASC"` by default. With `"ASC"`, documents that change during a long read move to the end, so the client does not skip them.

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
