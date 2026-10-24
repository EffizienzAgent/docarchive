A WORM document archive app for the Frappe Framework

# docarchive

docarchive files business documents such as invoices, contracts and bank statements in a Frappe site. It is meant to copy each one, unchanged, into write-once (WORM) object storage for its retention period.

## Status

This version has the document model, the locks, the storage configuration and the upload API. The nightly copy to Object Lock storage comes in a later release. Until then, nothing leaves the site.

## No compliance promise

docarchive is a technical tool. It does not make you compliant with GoBD, the GDPR or any other law or standard, and it gives no legal or tax advice. Retention periods, procedures and documentation are your responsibility. Check them with your tax advisor or auditor.

## How it works

- **Business Document**: one record per document, with a private file, a type, a document date, a number, an own company and a counterparty. The counterparty is a Dynamic Link (`counterparty_type` + `counterparty`), so it can point to any doctype the host app allows.
- **Checksums**: when a file is attached, the app computes `content_sha256` from the raw file bytes. The archive run compares this value with the bytes it reads back from storage.
- **Object Lock**: each archived object gets its own retention mode (GOVERNANCE or COMPLIANCE) and retain-until date. The app has no code path that deletes from an archive bucket. Object Lock only lets retention grow, never shrink.
- **Retention**: the retention period ends on January 1 after the document year plus the retention years. For contracts it can count from the contract end date instead. Years come from a rule per own company and document type in Archive Settings, or from the type's default. The seeded types (Outgoing Invoice, Incoming Invoice, Contract, Bank Statement) ship with editable defaults. Check them for your jurisdiction.
- **Locking**: once archiving starts (`archive_started_at`), the fields that make up the object key and the source fields can no longer change: own company, type, date, number, counterparty, file and `source_*`. The contract end date can still move later (which extends retention), but never earlier, and it cannot be cleared. Deleting the document is refused. Its file cannot be deleted, moved, made public or detached. Other File rows that share the archived object (the same `file_url` through deduplication, the same `content_hash`, or a `thumbnail_url` pointing at it) cannot change their URL, hash, thumbnail or privacy either. A counterparty linked from an archived document cannot be renamed or merged.
- **Server-only fields**: the checksum, the archive status, keys and retention fields, and the `source_*` fields are restored on every save, unless server code sets `doc.flags.archive_write`.
- **File types**: only the extensions listed in Archive Settings → Allowed File Types can be filed (default: pdf, png, jpg, jpeg, tif, tiff, xml). Both the file name and the file URL must match.
- **Access**: files are always private and attached to their Business Document, so Frappe's File permission follows the document permission. A user who sets or changes a counterparty needs read permission on it; an unknown counterparty gets the same answer as one without access. Queries go through `frappe.get_list`.

## Roles

The app creates the role **Document Manager** (read, write and create documents; no delete) and the role **Document Integration** for the [upload API](#upload-api) (no desk access and no document permissions; it can only call the upload endpoint). Only System Manager can read or change Archive Settings, so storage endpoints, buckets and key names stay hidden from document managers. To let a host role in, add it to **Archive Settings → Manager Roles** (Guest, All and Desk User are refused). The app then grants that role the same rights through Custom DocPerm and remembers which rows it created. On every save of the settings and on every migrate, it removes only those rows for roles that are no longer listed; permission rows set by an admin stay.

Custom DocPerm replaces the standard permissions of a doctype. If you edit permissions on these doctypes by hand, keep the manager rows.

## Storage targets

Archive Settings has a **Storage Targets** table with one row per bucket (usually two, in two regions). Each row has a name, an enabled flag and a lock mode (GOVERNANCE or COMPLIANCE). Endpoint, region and bucket come either:

- from the site config (`<prefix>_endpoint`, `<prefix>_region`, `<prefix>_bucket`), with the credentials in two named site config keys, or
- from fields on the row, with the credentials in Password fields, which Frappe stores encrypted.

Credentials are never stored in plain text. Endpoints must use https, must not be private, loopback or link-local addresses or local host names, and bucket names must be valid, for site config values once they are set too. Two targets cannot point to the same endpoint and bucket. The archive can only be enabled with at least one enabled target, and only when all its site config keys are set.

Any S3-compatible provider with Object Lock works. [docs/hetzner-example.md](docs/hetzner-example.md) shows one setup.

## Host app hooks

Add these to your app's `hooks.py`:

| Hook | Called with | Purpose |
|---|---|---|
| `docarchive_assign` | `doc` (Business Document, in `validate`, before archiving starts) | Fill `own_company`, `counterparty_type` and `counterparty`, e.g. from `source_system`, `source_account`, `source_party_type` and `source_party_id` (set by server code with `flags.archive_write`). Values the user set are kept. Do not save other documents here. |
| `docarchive_counterparty_doctypes` | – | List of doctypes allowed as counterparty. Empty: any doctype that is not a child table or a single. |
| `docarchive_read_file` | `file_url` | Return the raw bytes of a file, or `None`. Use this when files live outside the site folder (e.g. in object storage). The last hook wins. |

Own company resolution: an explicit value, then the `docarchive_assign` hooks, then the successor company by date, then the default company from Archive Settings.

File deletion runs inside the File controller, before any `doc_events` hook. So the delete guard also has to run there:

- With the `s3storage` app, docarchive registers `docarchive.guards.protect_file` under `s3storage_before_file_delete`, and `docarchive.guards.protect_object` under `s3storage_before_object_delete` and `s3storage_before_object_write`, which s3storage calls as `fn(file_url=..., key=...)` before it deletes or overwrites an object. So deletes and overwrites of an archived object (for example through `optimize_file`) are refused.
- If your app overrides the File class itself, call `docarchive.guards.protect_file(self)` at the start of `on_trash`, before `super().on_trash()`.
- Without either, deletes by users are still refused through `has_permission`. But a server-side delete with `ignore_permissions` would remove the bytes before the guard runs.

### Counterparty panel

The app's JS bundle offers a panel for any form: a list of the record's documents, an upload button (several files, drag and drop) and a dialog for type and date.

```js
frappe.ui.form.on("Customer", {
	refresh(frm) {
		docarchive.documents_panel(frm, "documents_html", "documents_section");
	},
});
```

`docarchive.render_documents($wrapper, { counterparty_type, counterparty })` and `docarchive.upload_documents({ counterparty_type, counterparty, on_saved })` are available for custom layouts.

### Dashboard helpers

- `docarchive.queries.documents_to_assign()`: documents that are not archived and still lack a file or a field their type requires, or whose archiving failed. Newest first, each with a `missing` list.
- `docarchive.queries.count_documents_to_assign()` (whitelisted): the number of those documents.

## Upload API

Integrations such as n8n create documents with one POST request:

```
POST /api/v2/method/docarchive.api.upload_document
Authorization: token <api_key>:<api_secret>
```

Create a dedicated user with only the role **Document Integration**, generate an API key and secret for it (User → Settings → API Access), and store them in the integration's credential store, never in a workflow. System Managers may call the endpoint too. The endpoint allows 120 requests per minute per IP.

Send the file either as multipart form data (field `file`) or as JSON / form fields with `content_base64` and `file_name`.

| Field | Required | Meaning |
|---|---|---|
| `file` or `content_base64` + `file_name` | yes | The document. The file name must end in an allowed file type (Archive Settings → Allowed File Types). The size limit is Frappe's maximum file size. |
| `document_type` | yes | The slug of a Business Document Type, e.g. `outgoing-invoice`. |
| `document_date` | yes | `YYYY-MM-DD`: invoice date, signing date or statement date. |
| `document_number` | if the type requires it | Invoice or contract number. |
| `contract_end_date` | if the type requires it | `YYYY-MM-DD`, only for types whose retention counts from the contract end. |
| `title` | no | Defaults to the file name. |
| `own_company` | no | Own Company slug. Empty: the host hook or the default from Archive Settings. |
| `source_system`, `source_account`, `source_party_type`, `source_party_id` | no | Where the document comes from, e.g. `Billing`, an account ID, `client`, `42`. The host app's `docarchive_assign` hook uses them to find the counterparty. |
| `source_party_name`, `source_party_email` | no | Shown to the people who assign the document by hand. Never used to guess a counterparty. |
| `source_key` | no | A stable ID of the document in the source system, e.g. `billing:invoice:42`. Makes retries safe. |

A type with **Counterparty Required** needs at least one of `source_party_id`, `source_party_name` and `source_party_email`. A counterparty that cannot be resolved does not fail the upload; the document then appears in `documents_to_assign()`.

Retries are safe:

- The same `source_key` with the same content returns the existing document (`"status": "exists"`).
- The same content for the same document type returns the existing document, with or without a `source_key`.
- A different file under a known `source_key` creates a new document whose `replaces` points to the latest version (`"status": "replaced"`). Existing documents are never changed, archived or not.

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
  -F source_key=billing:1:invoice:42
```

Response (HTTP 200):

```json
{"data": {
  "name": "DOC-2026-00017", "status": "created", "replaces": null,
  "document_type": "Outgoing Invoice", "own_company": "acme-gmbh",
  "counterparty_type": "Customer", "counterparty": "CUST-0042",
  "content_sha256": "9f2c…", "archive_status": "Pending", "missing": []
}}
```

`status` is `created`, `exists` or `replaced`. `missing` lists what the document still needs before it can be archived (`File`, `Counterparty`, `Document Number`, `Contract End Date`).

Errors come back as JSON with an HTTP status: 400 for an invalid request (unknown type, missing or invalid date, missing required field, file type, size, empty or damaged file), 403 without the role, 417 for other validation errors, 429 when rate limited:

```json
{"errors": [{"type": "UploadError", "message": "Unknown document type invoice."}]}
```

The older `/api/method/docarchive.api.upload_document` path works too; Frappe then returns errors as `exc_type` and `_server_messages`.

In n8n, use an HTTP Request node with method POST, a "Header Auth" credential (`Authorization: token key:secret`) and body type "Form-Data" with the binary field `file`, or body type JSON with `content_base64`.

## Installation

```bash
bench get-app https://github.com/EffizienzAgent/docarchive --branch version-16
bench --site <site> install-app docarchive
```

**Compatibility:** Frappe v16. The `version-16` branch always points to the latest release; tagged releases
and the changelog are on the [releases page](https://github.com/EffizienzAgent/docarchive/releases).

## Tests

```
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app docarchive
```

CI runs the suite on a Frappe v16 bench at the commit pinned in `.github/versions.env`.

## Professional setup & support

Need help setting this up or integrating it into your Frappe stack? Write to oss@effizienzagent.de (English or German). German-speaking SMEs: see [mittelstandshub.de](https://mittelstandshub.de).

## License

MIT
