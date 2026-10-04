# FAA DRS API — working notes

Sources: `drs-api-technical-design.pdf` (v6.0, 2025-08-07) and
`doctypes.csv`, plus live probes made on 2026-10-04.
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

- 105 doctypes across 8 services: FS (57), AIR (31), AST (5), OTHER (4: ICAO), AOV (3), AGC (2), ANG (2), ARM (1).
- 1,986 field rows. Types: ARRAY 1077, TEXT 659, DATE 191, **blank 60** (in FAR, AT_JTA, GA_JTA and
  OTHER_PROCEDURES_MANUAL, so these must be inferred).
- Each doctype has exactly one "default sort" field.
- The CSV header has typos and trailing spaces: `Deafult  Sort By`, `Document Type name in DRS `, `Metadata Name in API Response `.
- The doctype codes are not all valid Python identifiers (e.g. `ORDER_8900.1`, `AFS-1_MEMORANDUMS`, `8900.1_SUMMARY_OF_CHANGES`).

Latency is about 1.5–3 s per page; FAR took about 9 s.

## Findings from the full sweep (2026-10-04)

- **26 of 105 doctypes are internal only** for an external key (all ICAO, AOV, most `OTHER_*` and JTA types).
  This list is recorded in `scripts/generate_catalog.py`.
- The 79 readable types hold about 1.86M documents. **PMA alone has 1.64M**, then STC (83k), EXEMPTION (37k) and ADFRAWD (20k).
  `CANIC` and `FIRE_TEST_HANDBOOK_REVISION3` are currently empty.
- `documentGuid` is the only common field that is always present.
  - `docLastModifiedDate` is **null** for many documents in 24 types (e.g. 585 of 1,338 SAIBs).
  - PMA and TSOI documents have no file fields at all.
- **Inline full text** is returned for these doctypes, in their Summary / SupplementaryInfo / RegulatoryText / SCInfoText / SectionRule fields:
  ADFRAWD, ADNPRM, CFRFRSFAR, FAR, NPRM, SCFINAL, SCPROPOSED and SFAR.
  - The largest single field seen is about 775 KB (NPRM regulatory text).
  - `drs:nprmSupplementaryInfo` is an array, not a string.
- About 25 CSV field types are wrong compared with the live data (usually TEXT where the API returns ARRAY). FAR's types are blank in the CSV.
  Five live fields (e.g. `fsims:docLevel2` on the ORDER_8x00 handbooks) are missing from the CSV.
  The corrections are in `scripts/generate_catalog.py`.
- **Paging is stable**: full crawls of SAIB, ORDERS and BULLETINS under ASC, DESC and the default sort each returned exactly
  `totalItems` unique GUIDs.
- With `docLastModifiedDateSortOrder=ASC`, documents with a null date come first. With `DESC`, they come last
  (probed 2026-10-04 on SAIB and BULLETINS, GET and POST: one contiguous block in both cases, and full crawls
  in both directions returned `totalItems` unique GUIDs).
- `docLastModifiedDate` values are often shared: SAIB has 102 timestamps shared by 2 to 11 documents. Resuming a
  crawl from the last seen timestamp with the strict `docLastModifiedDate` filter would skip the rest of a group.
- The SDK pages with `DESC` by default. Offset paging with `ASC` can skip an unchanged document if a document that
  was already read changes mid-crawl (it moves to the end and everything after it shifts back one). With `DESC`
  a change can only repeat a document, and a document missed because it changed is newer than the first document
  read, so the next incremental sync gets it.
- `docLastModifiedDate` is **strictly after**, and it **excludes documents with a null date**
  (SAIB with a cutoff of 1900-01-01 returns 753 of 1,338).
- The `Keyword` filter returns documents that contain *any* of the given terms.
