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

The client checks these limits. If a query is not valid, the client raises `InvalidQueryError` and does not send a request. The client also checks the type of each value, for example that a date field has a `(start, end)` pair.

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

If a field name is not in the catalog, the client gives an `UnknownFieldWarning` that shows the nearest names. The client sends the filter anyway, because DRS can add fields before the catalog has them:

```text
UnknownFieldWarning: 'drs:saibIssuDate' is not in the catalog for document type 'SAIB'.
The filter is sent anyway. The API rejects fields that it does not know.
Did you mean: drs:saibIssueDate, drs:saibMake, drs:saibModel?
```

If the API does not know the field either, it rejects the request. The API message does not name the field, so the client adds the names to the `BadRequestError`:

```text
BadRequestError: [400] One or more filters provided are invalid or not applicable for the
requested document type. ... These filter fields are not in the catalog for SAIB: drs:saibIssuDate.
```

To make the warning an error, for example in tests:

```python
import warnings

from faa_drs import UnknownFieldWarning

warnings.simplefilter("error", UnknownFieldWarning)
```

To hide the warning for a field that you know is correct:

```python
warnings.filterwarnings("ignore", message="'drs:newField'", category=UnknownFieldWarning)
```

## Filter by change date

Use `modified_after` to get documents that changed after a time. See [Sync updates](sync.md).
