# API behavior

This page records how the DRS API operates. The FAA documentation does not include all of these items. The data comes from tests with an external API key in October 2026.

## Endpoints

| Method | Path | SDK method |
| --- | --- | --- |
| `GET` | `/{doctype}` | `list_documents`, `iter_documents` (no filters) |
| `POST` | `/{doctype}/filtered` | `list_documents`, `iter_documents` (with filters) |
| `GET` | `/download/{id}` | `download`, `download_to` |
| `GET` | `/get-other-attachment-details/{id}` | `get_attachments` |

The base URL is `https://drs.faa.gov/api/drs/data-pull`. The API key goes in the `x-api-key` header.

## Pages

- A page has a maximum of 750 documents. You cannot change the page size.
- An offset after the last document gives an empty page, not an error.
- Pages are stable. A full read gives each document one time.

## Errors

- Some errors have HTTP status 200. The error is in an `errorMessage` field in the body.
- A key that is not valid gives HTTP 403 with an empty body.

## Fields

- `documentGuid` is the only field that is always present.
- `docLastModifiedDate` is `null` for many documents.
- Some list fields contain the text `_EMPTY_`. The SDK changes it to `None` and keeps the position in the list.
- The FAA field list has some incorrect types. The SDK catalog contains corrections.

## Document types

- 26 of 105 document types are internal only. External keys cannot read them.
- 8 document types contain the full text inline. See [Text and files](../guides/files.md).

## Change date filter

- `docLastModifiedDate` returns documents that changed after the time. It does not include the time.
- It does not return documents that have no change date.
- With ascending sort, documents with no change date come first. With descending sort, they come last.
- Many documents can have the same change date. For example, 102 change dates in SAIB are shared by 2 to 11 documents.

## Filters

- A maximum of 5 filters, and a maximum of 10 values for each filter.
- Values in one filter: a document must match one value.
- Different filters: a document must match all filters.
- `Keyword`: a document must contain one or more of the terms.
