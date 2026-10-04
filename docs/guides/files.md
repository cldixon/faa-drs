# Text and files

The API supplies document text in one of two ways:

- **Inline text.** The text is in the metadata. Use `doc.content`.
- **A file.** The text is in a PDF or other file. Download the file.

## Inline text

These document types contain the full text inline:

| Code | Name |
| --- | --- |
| `ADFRAWD` | AD Final Rules |
| `ADNPRM` | AD Notices of Proposed Rulemaking |
| `CFRFRSFAR` | Final Rules |
| `FAR` | Title 14 CFR by Part and Section |
| `NPRM` | Notices of Proposed Rulemaking |
| `SCFINAL` | Special Conditions (Final) |
| `SCPROPOSED` | Special Conditions (Proposed) |
| `SFAR` | Special Federal Aviation Regulations |

`doc.content` is a dictionary of field name to text:

```python
doc = next(drs.iter_documents(DocType.FAR, limit=1))
print(doc.content["drs:farSectionRule"])
```

For other document types, `doc.content` is empty. Use `catalog.get_doctype(code).has_inline_content` to check a type.

## Download the main file

Download into memory:

```python
data = drs.download(doc)
```

Download to a directory. The client uses the file name from the document:

```python
path = drs.download_to(doc, "downloads/")
```

Download to a file path:

```python
path = drs.download_to(doc, "downloads/ac-39-8a.pdf")
```

The client writes to a temporary file, then renames it. If the download fails, no partial file stays at the destination.

Some documents have no file. For these, `doc.download_url` is `None` and `download` raises `DRSError`.

## Download attachments

Some documents have more files. `doc.has_attachments` is `True` for these documents.

```python
for attachment in drs.get_attachments(doc):
    drs.download_to(attachment, "downloads/")
```

If `has_attachments` is `False`, `get_attachments` returns an empty list and does not send a request.

## Extract text from files

This package does not extract text from PDF files. Use a PDF library for that step.

!!! note "Security"
    The client sends only the file ID to the configured API address. It does not send the API key to an address from response data.
