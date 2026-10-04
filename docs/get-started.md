# Get started

## Requirements

- Python 3.11 or later.
- A DRS API key.

## Get an API key

1. Go to [drs.faa.gov](https://drs.faa.gov).
2. Open **DRS Help & Training**, then **DRS API**.
3. Click **Request / Renew API Key**.
4. Follow the steps.

## Install

=== "uv"

    ```sh
    uv add faa-drs
    ```

=== "pip"

    ```sh
    pip install faa-drs
    ```

## Set the API key

Set the `DRS_API_KEY` environment variable:

```sh
export DRS_API_KEY="your-key"
```

The client reads this variable. You can also give the key directly:

```python
drs = DRSClient(api_key="your-key")
```

!!! warning
    Do not put the API key in source code that you share.

## Make a request

```python
from faa_drs import DRSClient, DocType

with DRSClient() as drs:
    page = drs.list_documents(DocType.AC)
    print(page.total)

    for doc in page.documents[:5]:
        print(doc.number, doc.status, doc.title)
```

`list_documents` gets one page. `iter_documents` gets all pages:

```python
with DRSClient() as drs:
    for doc in drs.iter_documents(DocType.AC, limit=100):
        print(doc.number)
```

Always close the client. Use a `with` block, or call `drs.close()`.

## Read a document

A `Document` has common fields as attributes:

| Attribute | Description |
| --- | --- |
| `guid` | Unique ID in DRS. Always present. |
| `doctype` | Document type code. |
| `number`, `title`, `status` | Values of `drs:documentNumber`, `drs:title` and `drs:status`. |
| `last_modified` | Last change in DRS (UTC). Can be `None`. |
| `url` | Page in the DRS web application. |
| `download_url`, `file_name` | Main file. `None` if there is no file. |
| `has_attachments` | `True` if there are more files. |
| `content` | Full text fields, if the API supplies them. |

Other fields are in `doc.metadata`. Use the API field name:

```python
doc["drs:approvalDate"]  # Raises KeyError if missing.
doc.get("drs:cancels")  # Returns None if missing.
doc.get_list("drs:partNumber")  # Always a list of strings.
doc.get_date("drs:approvalDate")  # A datetime.date, or None.
```

To find the fields of a document type, see [Filters](guides/filters.md#find-field-names).
