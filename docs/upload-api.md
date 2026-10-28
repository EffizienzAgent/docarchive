# Upload API

Integrations such as n8n create documents with one POST request:

```
POST /api/v2/method/docarchive.api.upload_document
Authorization: token <api_key>:<api_secret>
```

Create a dedicated user with only the role **Document Integration**, generate an API key and secret for it (User → Settings → API Access), and store them in the integration's credential store, never in a workflow. System Managers may call the endpoint too. The endpoint allows 120 requests per minute per IP.

Send the file either as multipart form data (field `file`) or as JSON / form fields with `content_base64` and `file_name`.

| Field | Required | Meaning |
|---|---|---|
| `file` or `content_base64` + `file_name` | yes | The main file. The file name must end in an allowed file type (Archive Settings → Allowed File Types). The size limit is Frappe's maximum file size. |
| `attachments[]` (multipart) or `attachments` (JSON) | no | Up to 20 further files with the same rules. Multipart: repeat the file field `attachments[]`, and optionally `attachment_roles[]` in the same order. JSON: a list of `{"file_name", "content_base64", "role", "title"}`. Roles: `Attachment` (default), `Structured Invoice (XML)`, `Proof of Service`, or their slugs (`structured-invoice-xml`). |
| `document_type` | yes, unless an e-invoice gives it | The slug of a Business Document Type, e.g. `outgoing-invoice`. |
| `document_date` | yes, unless an e-invoice gives it | `YYYY-MM-DD`: invoice date, signing date or statement date. |
| `document_number` | if the type requires it | Invoice or contract number. |
| `contract_end_date` | if the type requires it | `YYYY-MM-DD`, only for types whose retention counts from the contract end. |
| `title` | no | Defaults to the file name. |
| `own_company` | no | Own Company slug. Empty: the host hook or the default from Archive Settings. |
| `source_system`, `source_account`, `source_party_type`, `source_party_id` | no | Where the document comes from, e.g. `Billing`, an account ID, `client`, `42`. The host app's `docarchive_assign` hook uses them to find the counterparty. |
| `source_party_name`, `source_party_email` | no | Shown to the people who assign the document by hand. Never used to guess a counterparty. |
| `source_key` | no | A stable ID of the document in the source system, e.g. `billing:invoice:42`. Makes retries safe. |
| `legal_hold` | no | `true` or `false` (default). Sets **Set Legal Hold When Archiving**: every archived object is uploaded with a legal hold. |

A type with **Counterparty Required** needs at least one of `source_party_id`, `source_party_name` and `source_party_email`, or an e-invoice whose other party is known. A counterparty that cannot be resolved does not fail the upload; the document then appears in `documents_to_assign()`.

Retries are safe:

- The same `source_key` with the same content returns the existing document (`"status": "exists"`, only `name` and `status`). A `source_key` belongs to its `source_system` and `source_account`; reusing it from another source is refused. Parallel requests with the same `source_key` or the same content wait for each other, so they create one document; a request that waits longer than 30 seconds gets HTTP 409 and can retry.
- The same content for the same document type and the same `source_system` and `source_account` returns the existing document, with or without a `source_key`. Uploads from another source never match, update or replace it; they create their own document.
- Attachments are compared by SHA-256. When the main file is known and the request has attachments the document does not have yet: if archiving has not started, they are added to it (`"status": "updated"`); if it has, a new document with all sent files is created whose `replaces` points to it (`"status": "replaced"`). Attachments are never removed, so a request with fewer attachments returns `exists`.
- A different file under a known `source_key` creates a new document whose `replaces` points to the latest version (`"status": "replaced"`). Apart from added attachments, existing documents are never changed.

Example:

```bash
curl -X POST "https://<site>/api/v2/method/docarchive.api.upload_document" \
  -H "Authorization: token $API_KEY:$API_SECRET" \
  -F file=@invoice.pdf \
  -F document_type=outgoing-invoice \
  -F document_date=2026-03-15 \
  -F document_number=INV-2026-017 \
  -F source_system=Billing -F source_account=1 \
  -F source_party_type=client -F source_party_id=42 \
  -F source_key=billing:1:invoice:42 \
  -F "attachments[]=@timesheet.pdf" -F "attachment_roles[]=proof-of-service"
```

Response (HTTP 200):

```json
{"data": {
  "name": "DOC-2026-00017", "status": "created", "replaces": null,
  "document_type": "Outgoing Invoice", "own_company": "acme-gmbh",
  "counterparty_type": "Customer", "counterparty": "CUST-0042",
  "content_sha256": "9f2c…", "attachments": [{"role": "Proof of Service", "title": "timesheet", "content_sha256": "41d0…"}],
  "archive_status": "Pending", "missing": []
}}
```

`status` is `created`, `exists`, `updated` or `replaced`. `missing` lists what the document still needs before it can be archived (`File`, `Document Date`, `Counterparty`, `Document Number`, `Contract End Date`).

Errors come back as JSON with an HTTP status: 400 for an invalid request (unknown type, missing or invalid date, missing required field, file type, size, empty or damaged file), 403 without the role, 409 when a parallel request files the same document, 417 for other validation errors, 429 when rate limited, and 5xx for storage or internal errors (retry those):

```json
{"errors": [{"type": "UploadError", "message": "Unknown document type invoice."}]}
```

The older `/api/method/docarchive.api.upload_document` path works too; Frappe then returns errors as `exc_type` and `_server_messages`.

In n8n, use an HTTP Request node with method POST, a "Header Auth" credential (`Authorization: token key:secret`) and body type "Form-Data" with the binary field `file`, or body type JSON with `content_base64`.
