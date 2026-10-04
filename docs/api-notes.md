# FAA DRS API — working notes

Sources: `reference/DRS_API_Technical_Documentation.pdf` (v6.0, 2025-08-07) and
`reference/DRS_Document_Types_Metadata_Mapping.csv`, plus live probes made on 2026-10-04.
"Observed" means we saw it on the live API; the rest comes from the docs.

## Basics

- Base URL: `https://drs.faa.gov/api/drs/data-pull`
- Auth: `x-api-key: <key>` header. A missing or invalid key returns **403 with an empty body**.
- Data is refreshed every 24 hours.

## Endpoints

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET  | `/{doctype}` | List document metadata (paged) |
| 2 | GET  | `/download/{fileId}` | Download a file as a byte stream |
| 3 | GET  | `/get-other-attachment-details/{fileId}` | List the extra attachments for a document |
| 4 | POST | `/{doctype}/filtered` | List metadata, filtered by fields and keywords (paged) |

### 1. `GET /{doctype}`
Query params (all optional):
- `offset`: number of documents to skip. **The page size is fixed at 750** and there is no `limit` param.
- `docLastModifiedDate`: an ISO datetime such as `2021-05-09T14:38:11.964Z`. Returns only documents modified
  *after* this time, which makes incremental syncs possible.
- `docLastModifiedDateSortOrder`: `ASC` or `DESC`. Sorts by last-modified date. If omitted, results follow
  the doctype's default sort (the "Default Sort By" column in the CSV).

Response:
```json
{"summary": {"doctypeName", "drsDoctypeName", "count", "hasMoreItems", "totalItems",
             "offset", "sortBy", "sortByOrder"},
 "documents": [ {...}, ... ]}
```
- An offset past the end returns `count: 0`, `documents: []` and a normal summary (observed).

### 4. `POST /{doctype}/filtered`
JSON body (all optional; `{}` is valid):
```json
{"offset": 0, "docLastModifiedDate": "...Z", "sortOrder": "ASC|DESC",
 "documentFilters": {"drs:status": ["Current"], "drs:pmaSupDate": ["2020-01-01", "2025-07-31"],
                     "Keyword": ["corrosion"]}}
```
- Note the name difference: the sort param here is `sortOrder`, while GET uses `docLastModifiedDateSortOrder`.
- Filter values must always be arrays.
- DATE fields take exactly `[from, to]` in `YYYY-MM-DD` format.
- `Keyword` runs a full-text search over document content. Its terms are ORed (observed: SAIB + Current + "corrosion" → 142 results).
- Limits: **at most 5 filters, and at most 10 values per filter**. Keys must be valid for the doctype.
  Breaking these rules returns 400 `{"errorMessage": "One or more filters provided are invalid ..."}`.
- Null, empty or whitespace-only values are dropped silently.
- It is unclear how values within one filter combine, and how separate filters combine. The docs' examples suggest values are ORed and filters are ANDed.

### 2. `GET /download/{fileId}`
- The response is the raw file (observed: `Content-Type: application/pdf`, plus a `Content-Disposition` filename
  of the form `{documentGuid}.0001.{name}.pdf`).
- The docs list 404, 500 and 504 as possible errors and explicitly advise **retrying on 504**.
- The URLs come from each document's `mainDocumentDownloadURL` and `mainDocumentFileName` fields.

### 3. `GET /get-other-attachment-details/{fileId}`
- `{fileId}` is the id of the *main* document file, i.e. the tail of `mainDocumentDownloadURL`.
- Response (observed): `{"otherAttachmentDownloadDetails": [{"fileDownloadURL", "fileName"}, ...]}`.
- If no attachments are available to an external key, the docs show a `{"comments": "..."}` body instead.
- Only call this when `hasMoreAttachments` is true. In that case the document also has an `otherAttachmentDetailsUrl` field.

## Errors (important quirk)

Application errors can come back with **HTTP 200** and an `{"errorMessage": "..."}` body, with no `summary`.
Observed: `GET /NOPE` → `200 {"errorMessage":"The doctype NOPE is not present in DRS"}`.
The documented messages are:
- `The doctype <X> is not present in DRS` → unknown doctype
- `Could not process the request. The doctype <X> is internal only doctype` → our key can't access it
- `Unable to retrieve documents due to system error, ...` → server-side failure, worth retrying
- `One or more filters provided are invalid ...` (400) → invalid filtered query

So the client must check the body for `errorMessage` and not rely on the status code alone.
No rate-limit headers were observed.

## Document shape

Every document has these common fields:
`docLastModifiedDate` (UTC ISO), `documentGuid`, `documentURL` (DRS browse page),
and, when a file exists, `mainDocumentDownloadURL`, `mainDocumentFileName`,
`hasMoreAttachments`, and `otherAttachmentDetailsUrl` (the last one only when `hasMoreAttachments` is true).

All other fields are namespaced metadata (`drs:`, `fsims:`, `faagov:`) and depend on the doctype. Their types:
- `TEXT` → `str | null`
- `ARRAY` → `list[str]`; may be `[]` or `null`
- `DATE` → `"YYYY-MM-DD"` string
- **Sentinel**: arrays can contain the literal `"_EMPTY_"` (observed: AC `drs:subPart` and `drs:sectionNumber`, where the
  entries line up position-by-position with `drs:partNumber`). These should be treated as None, but list positions must be kept.

For SAIB and AC, the live response keys matched the CSV exactly.

## Inline text vs. PDF-only

Some doctypes carry the full document text in their metadata. Observed:
- `ADFRAWD` (AD Final Rules): `drs:adfrawdRegulatoryText`, `drs:adfrawdSupplementaryInfo` and `drs:adfrawdSummary`
  (up to ~65 KB). This makes the payload heavy: one page of 750 documents is about 19.7 MB.
- `FAR`: `drs:farSectionRule` holds the full section text, newline-separated.
- `AC`, `SAIB` and `ORDER_8900.1`: metadata only, with the content in a PDF via `mainDocumentDownloadURL`.

The CSV does not mark which fields hold body text (they are all just `TEXT`). The SDK should keep its own small
curated map of the "content fields" for each doctype.

## Document types (CSV)

- 105 doctypes across 8 services: FS (65), AIR (32), AST (5), OTHER (4: ICAO), AOV (3), AGC (2), ANG (2), ARM (1).
- 1,986 field rows. Types: ARRAY 1077, TEXT 659, DATE 191, **blank 60** (in FAR, AT_JTA, GA_JTA and
  OTHER_PROCEDURES_MANUAL, so these must be inferred).
- Each doctype has exactly one "default sort" field.
- The CSV header has typos and trailing spaces: `Deafult  Sort By`, `Document Type name in DRS `, `Metadata Name in API Response `.
- The doctype codes are not all valid Python identifiers (e.g. `ORDER_8900.1`, `AFS-1_MEMORANDUMS`, `8900.1_SUMMARY_OF_CHANGES`).
- Some doctypes may be internal-only for external keys. This hasn't been tested yet; we need to sweep all 105.

Sizes seen so far: SAIB 1,338; AC 1,686; ALERTS 109; ORDER_8900.1 5,092; FAR 12,776; ADFRAWD 20,168.
Latency is about 1.5–3 s per page; FAR took about 9 s.

## Open questions / to verify

- [ ] Sweep all 105 doctypes and record which return `internal only` for our key.
- [ ] Check whether paging with the default sort is stable over large offsets. If not, use `ASC` by last-modified date when doing a bulk pull.
- [ ] Check whether `docLastModifiedDate` is strictly "after", and how it combines with filters.
- [ ] Learn how a doc's revisions/versions show up: is each revision a separate document with the same number?
- [ ] Find out if there is any rate limiting. Neither the headers nor the docs mention any.
