# Filter and search

## Filter by field

Use `filters` to get only the documents that match:

```python
docs = drs.iter_documents(
    DocType.AC,
    filters={"drs:status": "Current", "drs:partNumber": ["Part 25", "Part 23"]},
)
```

- A document matches a filter if its field has one of the values.
- A document must match all filters.
- Give one value as a string, or more values as a list.

## Filter by date

Give a date field a `(start, end)` pair. The range includes both dates.

```python
from datetime import date

docs = drs.iter_documents(
    DocType.SAIB,
    filters={"drs:saibIssueDate": (date(2024, 1, 1), date(2024, 12, 31))},
)
```

You can also use `"YYYY-MM-DD"` strings.

## Search the full text

Use `keywords` to search the document content. A document matches if it contains one or more of the terms.

```python
docs = drs.iter_documents(DocType.SAIB, keywords=["corrosion", "fatigue"])
```

## Limits

The API sets these limits:

- A maximum of 5 filters. `keywords` counts as 1 filter.
- A maximum of 10 values for each filter.

The client checks these limits. If a query is not valid, the client raises `InvalidQueryError` and does not send a request.

## Find field names

Field names are different for each document type. Use the catalog to find them:

```python
from faa_drs import catalog

info = catalog.get_doctype("SAIB")
for field in info.fields.values():
    print(field.key, field.type, field.label)
```

```text
drs:status TEXT Status
drs:title TEXT Subject
drs:revNum TEXT Revision Number
drs:productType ARRAY Product Type
...
```

If a field name is not correct, the error message shows the nearest names:

```text
InvalidQueryError: 'drs:saibIssuDate' is not a filterable field for document type 'SAIB'.
Did you mean: drs:saibIssueDate?
```

## Filter by change date

Use `modified_after` to get documents that changed after a time. See [Sync updates](sync.md).
