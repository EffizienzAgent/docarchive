A WORM document archive app for the Frappe Framework

# docarchive

docarchive files business documents such as invoices, contracts and bank statements in a Frappe site. It is meant to copy each one, unchanged, into write-once (WORM) object storage for its retention period.

## Status

This version has the document model, the locks, the storage configuration, the upload API, the nightly archive run to Object Lock storage, legal holds, and the deletion of expired documents. Archiving is off until a System Manager enables it in Archive Settings. Until then, nothing leaves the site.

## No compliance promise

docarchive is a technical tool. It does not make you compliant with GoBD, the GDPR or any other law or standard, and it gives no legal or tax advice. Retention periods, procedures and documentation are your responsibility. Check them with your tax advisor or auditor.

## How it works

- **Business Document**: one record per document, with a private file, a type, a document date, a number, an own company and a counterparty. The counterparty is a Dynamic Link (`counterparty_type` + `counterparty`), so it can point to any doctype the host app allows.
- **Attachments**: a document has one main file (`file`, used for the preview and the title) and optional **Attachments**: further files such as annexes, the XML of an e-invoice or a proof of service. Each row has a file, a role (Attachment, Structured Invoice (XML), Proof of Service), a title (defaults to the file name) and its SHA-256. Attachments follow the same rules as the main file: private, uploaded to this document by the same user (exact URL and owner), an allowed file type, and no file twice in one document. They are archived, locked, extended and checked together with the main file. Once archiving starts, the attachment list is locked: no row can be added, removed, reordered or changed. An addendum becomes a new document, e.g. with `replaces` set by the upload API.
- **Checksums**: when a file is attached, the app computes `content_sha256` from the raw file bytes. The archive run compares this value with the bytes it reads back from storage.
- **Object Lock**: each archived object gets its own retention mode (GOVERNANCE or COMPLIANCE) and retain-until date. With the regular archive key the app never deletes from an archive bucket; its only delete call is the smoke check, which tries to delete its own `_smoke/` test object and expects a refusal. Deleting expired documents needs a separate, optional archive admin key (see [Deleting expired documents](#deleting-expired-documents)). Object Lock only lets retention grow, never shrink.
- **Retention**: the retention period ends on January 1 after the document year plus the retention years. For contracts it can count from the contract end date instead. Years come from a rule per own company and document type in Archive Settings, or from the type's default. The seeded types (Outgoing Invoice, Incoming Invoice, Contract, Bank Statement) ship with editable defaults. Check them for your jurisdiction.
- **Locking**: once archiving starts (`archive_started_at`), the fields that make up the object key and the source fields can no longer change: own company, type, date, number, counterparty, file, the attachments and `source_*`. The contract end date can still move later (which extends retention), but never earlier, and it cannot be cleared. Deleting the document is refused. Its file cannot be deleted, moved, made public or detached. Other File rows that share the archived object (the same `file_url` through deduplication, the same `content_hash`, or a `thumbnail_url` pointing at it) cannot change their URL, hash, thumbnail or privacy either. A counterparty linked from an archived document cannot be renamed or merged.
- **Server-only fields**: the checksum, the archive status, keys and retention fields, and the `source_*` fields are restored on every save, unless server code sets `doc.flags.archive_write`.
- **Plausible dates**: a document date may be at most **Max. Days in the Future** (default 31) after today, and a contract end at most **Max. Contract Years** (default 50) after the document date.
- **File types**: only the extensions listed in Archive Settings → Allowed File Types can be filed (default: pdf, png, jpg, jpeg, tif, tiff, xml). Both the file name and the file URL must match.
- **Access**: files are always private and attached to their Business Document, so Frappe's File permission follows the document permission. A user who sets or changes a counterparty needs read permission on it; an unknown counterparty gets the same answer as one without access. Queries go through `frappe.get_list`.

## Roles

The app creates the role **Document Manager** (read, write and create documents; no delete), the role **Archive Admin** (read documents, release legal holds, delete documents after their retention; see [Legal hold](#legal-hold)) and the role **Document Integration** for the [upload API](#upload-api) (no desk access and no document permissions; it can only call the upload endpoint). Files attached to a Business Document can only be changed or deleted by users who may write that document, so an integration user cannot touch the files it uploaded. Only System Manager can read or change Archive Settings, so storage endpoints, buckets and key names stay hidden from document managers. To let a host role in, add it to **Archive Settings → Manager Roles** (Guest, All and Desk User are refused). The app then grants that role the same rights through Custom DocPerm and remembers which rows it created. On every save of the settings and on every migrate, it removes only those rows for roles that are no longer listed; permission rows set by an admin stay.

Custom DocPerm replaces the standard permissions of a doctype. If you edit permissions on these doctypes by hand, keep the manager rows.

## Storage targets

Archive Settings has a **Storage Targets** table with one row per bucket (usually two, in two regions). Each row has a name, an enabled flag and a lock mode (GOVERNANCE or COMPLIANCE). Endpoint, region and bucket come either:

- from the site config (`<prefix>_endpoint`, `<prefix>_region`, `<prefix>_bucket`), with the credentials in two named site config keys, or
- from fields on the row, with the credentials in Password fields, which Frappe stores encrypted.

Credentials are never stored in plain text. Endpoints must use https, must not be private, loopback or link-local addresses or local host names, and bucket names must be valid, for site config values once they are set too. Two targets cannot point to the same endpoint and bucket. The archive can only be enabled with at least one enabled target, and only when all its site config keys are set.

Each row can also name an optional **archive admin key**: in site config mode two more config key names (**Admin Access Key Config Key**, **Admin Secret Key Config Key**, e.g. `s3_archive_admin_access_key` and `s3_archive_admin_secret_key`), in fields mode two more Password fields. Both or neither must be set. Archiving never uses the archive admin key. It is only used to release legal holds and to delete documents. When it is not configured, or its site config keys are empty, those actions are hidden and refused, and everything else keeps working.

### Two keys

| | Archive key (`access_key`) | Archive admin key (`admin_access_key`) |
|---|---|---|
| Used for | archiving (optionally with a legal hold at upload), extending retention, smoke check | setting and releasing legal holds on archived objects, deleting expired documents, early GOVERNANCE delete |
| Needs | `PutObject` (with `ObjectLockLegalHoldStatus` for a hold at upload), `PutObjectRetention`, `GetObject*`, `ListBucket` | `PutObjectLegalHold`, `DeleteObjectVersion`, `GetObject*`, `ListBucket`; `BypassGovernanceRetention` only for early deletion |
| Never | delete, shorten retention, bypass governance, change policies | `PutObject`, `DeleteObject` without version (delete markers), `PutObjectRetention`, policy or lock changes |

S3 has a single permission, `s3:PutObjectLegalHold`, for setting and releasing a hold on an existing version, and providers such as Ceph RGW (Hetzner) do not evaluate the `s3:object-lock-legal-hold` condition that could limit a key to ON. So only the archive admin key changes holds on archived objects, in both directions. The archive key can only set a hold while it uploads a new version (`PutObject` with `ObjectLockLegalHoldStatus=ON`); the AST guard keeps `put_object_legal_hold` away from the archive key.

Any S3-compatible provider with Object Lock works. [docs/hetzner-example.md](docs/hetzner-example.md) shows one setup. `scripts/apply_bucket_policy.py` sets a deny-only bucket policy for the keys, and `scripts/check_object_lock.py` checks what each key can and cannot do in one bucket.

## Host app hooks

Add these to your app's `hooks.py`:

| Hook | Called with | Purpose |
|---|---|---|
| `docarchive_assign` | `doc` (Business Document, in `validate`, before archiving starts) | Fill `own_company`, `counterparty_type` and `counterparty`, e.g. from `source_system`, `source_account`, `source_party_type` and `source_party_id` (set by server code with `flags.archive_write`). Values the user set are kept. Do not save other documents here. |
| `docarchive_counterparty_doctypes` | – | List of doctypes allowed as counterparty. Empty: any doctype that is not a child table or a single. |
| `docarchive_read_file` | `file_url` | Return the raw bytes of a file, or `None`. Use this when files live outside the site folder (e.g. in object storage). The last hook wins. |
| `docarchive_einvoice_extractors` | `data`, `file_name` | Return e-invoice metadata or `None`. See [E-invoices](#e-invoices). |

Own company resolution: an explicit value, then the `docarchive_assign` hooks, then the successor company by date, then the default company from Archive Settings. While a document has no date, the successor and default companies are not applied: own company stays empty and is listed as missing until the date is set.

File deletion runs inside the File controller, before any `doc_events` hook. So the delete guard also has to run there:

- With the `s3storage` app, docarchive registers `docarchive.guards.protect_file` under `s3storage_before_file_delete`, and `docarchive.guards.protect_object` under `s3storage_before_object_delete` and `s3storage_before_object_write`, which s3storage calls as `fn(file_url=..., key=...)` before it deletes or overwrites an object. So deletes and overwrites of an archived object (for example through `optimize_file`) are refused.
- If your app overrides the File class itself, call `docarchive.guards.protect_file(self)` at the start of `on_trash`, before `super().on_trash()`.
- Without either, deletes by users are still refused through `has_permission`. But a server-side delete with `ignore_permissions` would remove the bytes before the guard runs.

### Counterparty panel

The app's JS bundle offers a panel for any form: the record's documents with search, filters (type, year, status) and sorting, row actions (preview, open in a new tab, copy link, archive now) and an upload with drag and drop. The upload dialog has one row per file with title, type and date. A date in the file name (`2026-08-05`, `2026_08_05`, `20260805` or `05.08.2026`) becomes the document date and is left out of the title. Uploads without a date take it from an e-invoice or the file name on the server too; otherwise the document stays without a date and is listed as missing it. With several files, the dialog can file them as one document: the first row becomes the main file, the others its attachments.

```js
frappe.ui.form.on("Customer", {
	refresh(frm) {
		docarchive.documents_panel(frm, "documents_html", "documents_section");
	},
});
```

`docarchive.render_documents($wrapper, { counterparty_type, counterparty })` and `docarchive.upload_documents({ counterparty_type, counterparty, files, on_saved })` are available for custom layouts. Set `docarchive.file_url = (file_url) => url` to open files in your own viewer.

Rows the user can act on have a checkbox. A bar above the list then offers "Archive now", "Extend term", "Legal Hold" and, for Archive Admins, "Export" for the selected rows; rows without any of these show a disabled checkbox with the reason. Clicking a row opens a preview pane on the right (an overlay on narrow screens) with a fixed header (the same labelled fields for every document, empty ones as "–") and the main file and attachments as tabs. Up/Down or j/k move through the list, Esc closes, and the pane can be resized. PDFs and images show inline, other files as a download. HTML is never rendered by docarchive itself: set `docarchive.html_preview = (file_url, title) => Promise<HTMLIFrameElement>` to return a sandboxed frame from your app, otherwise the pane links to `docarchive.file_url`.

### Triage

`docarchive.triage(names, { start, on_close })` opens a dialog that walks through documents one by one: the fields and what is still missing on the left, and the file on the right (PDF and images inline, other types as a download). "Save and next" (Ctrl/Cmd+Enter), "Skip", "Back" and Alt+Left/Right move through the list; Esc closes. "Open document" opens the form in a new tab, so the dialog and unsaved input stay; documents that are not archived yet can be deleted from the dialog. Each save is a normal `doc.save()` with permission checks, and validation errors stay inline. `docarchive.triage_all()` opens it on every document that still needs work. The Business Document list has "Assign" for selected documents and "Assign all".

### Dashboard helpers

- `docarchive.queries.documents_to_assign()`: documents that are not archived and still lack a file, a date or a field their type requires, or whose archiving failed. Newest first, each with a `missing` list.
- `docarchive.queries.count_documents_to_assign()` (whitelisted): the number of those documents.
- `docarchive.expiry.due_count()` (whitelisted): the number of documents due for deletion (the rows of **Expired Documents**). The standard number card **Documents Due for Deletion** shows it.
- The document list (`counterparty_documents`) returns `legal_hold`, so panels can show the hold. Deleted documents are left out.

## E-invoices

docarchive recognizes e-invoices among a document's files (main file first, then the attachments):

- XRechnung / EN 16931 XML in **CII** (UN/CEFACT Cross Industry Invoice) or **UBL** (Invoice and CreditNote) syntax,
- **ZUGFeRD / Factur-X** PDFs: the embedded XML (`factur-x.xml`, `zugferd-invoice.xml`, `xrechnung.xml` or another embedded `.xml`) is read from the PDF's embedded files. The PDF is not changed.

**What is archived**: the files as they are. A ZUGFeRD PDF is archived whole, with its embedded XML, exactly as before. With **Archive Settings → Store E-Invoice XML as Attachment** (off by default), the embedded XML is also stored as an attachment with the role *Structured Invoice (XML)*, so it is archived as its own object next to the PDF.

**What is extracted** (metadata only, no line items): invoice number, issue date, due date, type code and whether it is an invoice or a credit note; seller and buyer name, VAT IDs, tax numbers and address; currency; net, tax and gross totals and the amount due; the buyer reference (Leitweg-ID); the syntax and profile. The data is stored in `einvoice_data`, shown in the **E-Invoice** section of the form and written to the `.meta.json` under `einvoice`. It is frozen once archiving starts. The `.meta.json` is a WORM object, so the extracted data (including the parties' names, addresses and IDs) becomes permanent there for the whole retention period.

**How it is used**, only while archiving has not started and only when the files change:

- Empty fields are prefilled: document date, number, title, own company and type. Add your VAT IDs to **Own Company → VAT IDs**: if the seller's VAT ID matches, the document is an **Outgoing Invoice** of that company, if the buyer's matches, an **Incoming Invoice** (by the seeded type slugs `outgoing-invoice` and `incoming-invoice`). Tax numbers are not used for this, and when own VAT IDs appear on both sides nothing is filled.
- Own company and type taken from the XML are only suggestions, since anyone can send a crafted XML. The document gets **E-Invoice Needs Confirmation** and lists "Confirmation" as missing, so it is never archived automatically. A person confirms by saving it in the form or the triage with both fields set. Type and own company sent by the upload API are trusted as before.
- Values a user or the API set are never overwritten. When they differ from the XML, the form shows it, e.g. "Document Date in the XML differs: 10.03.2026". The upload API only needs `document_type` and `document_date` when the e-invoice does not provide them.
- The counterparty hint goes to your `docarchive_assign` hook: call `docarchive.einvoice.counterparty_hint(doc)`. It returns the other party (the buyer of an outgoing, the seller of an incoming invoice) as `{"vat_id", "tax_number", "name", "address"}`. Match by VAT ID first, then by tax number, then by name. Documents with e-invoice data but no counterparty are run through the hooks again before each nightly run.

**Existing documents**: `bench --site <site> docarchive-extract [--dry-run]` reads the e-invoice data of documents whose archiving has not started and that have none yet. It fills only empty fields (and the title while it is still the automatic one) and records conflicts. A document without an e-invoice is marked as checked without changing its `modified` date, so it is not read again. `--dry-run` lists what it would fill and writes nothing. It exits with status 1 if a document fails.

**Your own parser**: register callables under `docarchive_einvoice_extractors` in `hooks.py`. Each gets `(data: bytes, file_name: str)` and returns a dict in the format above (see `docarchive/einvoice_parser.py`) or `None`. The result is validated: dates must be ISO dates, texts are cut to 140 characters, unknown keys are dropped, and an optional `document_type` must be `outgoing-invoice` or `incoming-invoice`. The last hook is tried first, the built-in parser last. An ERPNext site can plug in a dedicated e-invoice app this way. To store the XML of a PDF as an attachment, return it as `xml` together with `embedded_file`.

**Safety**: XML is parsed with `defusedxml` (no DTDs, no entities, no network) with explicit namespaces. XML larger than 10 MB, also inside a PDF, is skipped, and at most 5 embedded XML files of a PDF are tried, known file names first. Files that cannot be read are treated as plain documents; saving never fails because of them.

**GoBD note**: for a hybrid invoice such as ZUGFeRD, the structured XML is the authoritative original, not the PDF view. Archive the file you received unchanged; docarchive does that. The extracted metadata is a convenience for filing and search. docarchive does not validate e-invoices against EN 16931 or XRechnung, does not check that the PDF view matches the XML, and makes no compliance promise (see [No compliance promise](#no-compliance-promise)).

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

- The same `source_key` with the same content returns the existing document (`"status": "exists"`, only `name` and `status`). A `source_key` belongs to its `source_system` and `source_account`; reusing it from another source is refused. Two parallel requests with the same new `source_key` create one document.
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

Errors come back as JSON with an HTTP status: 400 for an invalid request (unknown type, missing or invalid date, missing required field, file type, size, empty or damaged file), 403 without the role, 417 for other validation errors, 429 when rate limited, and 5xx for storage or internal errors (retry those):

```json
{"errors": [{"type": "UploadError", "message": "Unknown document type invoice."}]}
```

The older `/api/method/docarchive.api.upload_document` path works too; Frappe then returns errors as `exc_type` and `_server_messages`.

In n8n, use an HTTP Request node with method POST, a "Header Auth" credential (`Authorization: token key:secret`) and body type "Form-Data" with the binary field `file`, or body type JSON with `content_base64`.

## Archive run

Every night at 02:30 (server time) the scheduler queues `docarchive.run.nightly` on the `long` queue. It does nothing unless **Archive Settings → Enabled** is set and every enabled storage target is fully configured.

A document is archived when its type has **Archive** set, nothing is missing (file, document date, and the counterparty, number or contract end date its type requires), it has not changed for **Minimum Age (Hours)** (default 24), and it is not archived yet. Documents whose archiving already started are retried every night, whatever their age. Before archiving, documents with source fields but no counterparty run through the `docarchive_assign` hooks again.

For each document the run:

1. gets the bytes and the file extension from `docarchive.content.content_for(doc)` and checks that they still match `content_sha256`,
2. computes the retention (see below) and checks it against every target's lock mode, before anything is written,
3. sets `archive_started_at`, the object keys, the checksum, `retain_until` and the metadata, and commits. From now on the document is locked,
4. writes the main file, each attachment and then the `.meta.json` to every enabled target and records each object's version ID. It then reads that version back and checks its lock mode, retain-until date and SHA-256 (for every object),
5. sets **Archived** when every enabled target holds all objects. Otherwise it sets **Failed** with the error per target; the next run retries only the failed targets.

Each target has its own row under **Archive Copies** with its status, lock mode, retain-until date, the version IDs of the main file and the `.meta.json`, and the error. The version ID of each attachment on each target is under **Attachment Copies**. A target added later gets its copy on the next run, also for documents that are already archived. A document is processed by one run at a time (a lock per document), so the nightly run and "Archive now" cannot write twice. Before writing, the run sends a HEAD request: an object with the same key, the same SHA-256 and the expected lock counts as done. The same key with a different SHA-256, a weaker lock or a version other than the recorded one is a hard error that goes to the Error Log. Nothing is ever overwritten.

### Object keys

```
<own-company>/<year>/<type-slug>/<counterparty-id>/<date>_<number>_<document-id>.<ext>
<same key without extension>__att-<n>-<title>.<ext>
<same key>.meta.json
```

For example `acme-gmbh/2026/outgoing-invoice/cust-0042/2026-03-15_inv-2026-017_doc-2026-00017.pdf`. Each segment is lowercased and transliterated, and every character other than `a-z` and `0-9` becomes a hyphen. A document without a counterparty goes to `ohne-gegenpartei`, one without a number gets `ohne-nummer`. An attachment goes next to the main file, numbered in table order and named after its title (or its role), e.g. `…_doc-2026-00017__att-1-timesheet-march.pdf`. The key only uses fields that are locked once archiving starts, and attachment keys are stored at archive start, so a retry always writes the same keys.

The `.meta.json` holds the document data (ID, title, type, date, number, contract end, own company), the counterparty, the main file (`file`), every file in `files` (main file and attachments, each with key, name, role, SHA-256 and size, so the set is provable), the source fields (including the optional source record `source_doctype` / `source_name`), and the retention (counts from, start date, years, retain-until date, mode). It is frozen at archive start (`archive_meta`), so retries write the same bytes.

### Retention

`retain_until` is January 2, 00:00 UTC, of the year after (start year + retention years). The extra day covers the end of the year in every time zone. The start is the document date, or the contract end date for types whose retention counts from it. Years come from the Archive Settings rule for own company and type, else the type's default. Without either, the document fails with an error and nothing is written.

### Contract end and retention extension

After archiving starts, the contract end date can be set for the first time or moved later, never earlier. Each night, after archiving, the run recomputes `retain_until` for archived documents whose type counts from the contract end, using the retention years stored at archive start. When the new date is later than a copy's date, it extends the retention of the main file, every attachment and the `.meta.json` on that target with `PutObjectRetention`, in the same mode. Each copy records its own date. The document's `retain_until` moves only when every enabled target has the new date, and a failed target is retried the next night. A shorter date never leads to a request.

Each extension also writes a sidecar `<key>.meta.<n>.json` (n = 2, 3, …) to every target, locked with the new date and the copy's mode. It holds the metadata with the new retention, the extension (previous and new date, contract end, time) and the earlier extensions, so the newest sidecar carries the whole trail. The original `.meta.json` is never rewritten: Object Lock versions are immutable, and a second version under the same key would hide the first. The sidecar content is frozen before the first write, so a retry writes the same bytes, and a contract end that moves again in the meantime leads to the next extension. Before extending, the run checks the SHA-256 of the exact object version it recorded.

### Manual extension

Document managers can extend the retention of archived documents by hand: "Extend retention" on the form (add N years or pick a date), "Extend retention" in the Business Document list and "Extend all documents of this counterparty" in the panel (at least N years from the retention start, so each document gets the later of its current date and start + N years, or a fixed date). Bulk extensions show a preview with the current and new date per document and the skipped ones, then run as a background job with progress and a summary.

A picked date counts inclusively: the copies stay locked until the end of that day (00:00 UTC the next day). The server refuses any date that is not later than every archived copy's current date or more than 100 years ahead, refuses documents with a copy on a target that is no longer enabled and deleted documents, and never shortens; the document's `retain_until` never moves earlier. A manual extension uses the same path as the contract end extension above: `extend_locked` per copy, a locked `.meta.<n>.json` sidecar, per-target status, and the nightly run retries targets that failed. The extension entry also records who extended, when and why (optional reason). While one extension is still running on a document, a new one is refused. The endpoints in `docarchive.extension` are POST only and check the manager role and write access.

### Retention overview

The Business Document list and the counterparty panel show `retain_until`; the panel filters and sorts by it. The report "Retention Overview" lists archived documents with a chart by retain-until year and filters for type, own company, counterparty, lock mode, "ends within N months" and failed copies. The number card "Retention ends in 90 days" and `docarchive.extension.count_ending(days)` give hosts a count to show.

### Object Lock writes

`docarchive.run.locked_objects(doc, copy)` lists every locked object version of a copy as `(key, version_id, sha256)`: the main file, each attachment and the `.meta.json` (extension sidecars are recorded separately, in `sidecar_versions`). Code that acts on all objects of a document, such as the retention extension and the mode sync, goes through it. `run.copy_objects(copy, doc)` adds the sidecars; legal holds and deletion use it, so they cover the main file, every attachment, the `.meta.json` and every sidecar.

`docarchive.store.put_locked(target, key, body, mode, retain_until)` is the only code that calls `PutObject`. Before any request it checks that the mode is `GOVERNANCE` or `COMPLIANCE` and that `retain_until` is a timezone-aware datetime in the future. It always sends `ObjectLockMode`, `ObjectLockRetainUntilDate`, `ContentMD5` and the SHA-256 as object metadata. `docarchive.store.extend_locked` is the only code that calls `PutObjectRetention`; see the retention extension above. A test parses the app's source: each method of each client (archive key `_client`, archive admin key `_admin_client`) may be used in exactly one function of `store.py`. The archive key never calls `PutObjectLegalHold`; `put_locked` can send `ObjectLockLegalHoldStatus=ON` at upload, and `smoke_delete` is its only delete. With the archive admin key: `_admin_hold` changes holds and is only called by `set_hold` and `release_hold`, and `_admin_delete` deletes a version and is only called by `delete_expired` and `delete_early`. The admin client may not be referenced anywhere else, so it cannot be aliased into another function. `BypassGovernanceRetention` is only allowed in `smoke_delete` and `delete_early`. Copy, multipart, presigned URL and policy calls are banned, and only `store.py` may import boto3, botocore or `s3storage.client` or touch a client.

The lock mode comes from the storage target and is recorded per copy. Switch a target to COMPLIANCE once your trial is done. New documents then use COMPLIANCE. Existing objects are switched outside the app with the offline admin key (see [docs/hetzner-example.md](docs/hetzner-example.md)). Then run `bench --site <site> docarchive-sync-mode --target <name>`: it reads the retention of every recorded object version (main file, attachments, `.meta.json`) of every archived copy on that target. It only records an upgrade from GOVERNANCE to COMPLIANCE when all of them report COMPLIANCE, never a downgrade. Documents with any other difference are listed as mismatched, errors are listed per document, and the command exits with status 1 if there are any.

### Legal hold

**Set Legal Hold When Archiving** ("Legal Hold beim Festschreiben setzen") is a checkbox on the document and the `legal_hold` field of the upload API. It can be changed until archiving starts. When it is set, the archive run uploads every object (file, attachments, `.meta.json`, and sidecars written later) with `ObjectLockLegalHoldStatus=ON` and checks the hold with the versioned HEAD after the upload. This works with the archive key alone, so it is the only hold option without an archive admin key.

**Set Legal Hold** (form, and list action for selected documents) puts an already archived document on hold. It needs a reason and write permission on the document (or the Archive Admin role), and the archive admin key on every target with a copy; without an archive admin key the action is hidden and refused. It sets the hold to ON on the recorded version of the file, every attachment, the `.meta.json` and every extension sidecar, on every target, with the archive admin key, and reads it back with a versioned HEAD. Each copy records its own state (`Off`, `On`, `Failed`). The document counts as held (`legal_hold`, shown and filterable in the list) as soon as one copy holds. Copies added later and sidecars written by a later extension are uploaded with the hold while the document is held. Setting it again retries the copies that failed.

**Release Legal Hold** is only for the role **Archive Admin**, needs a reason and a confirmation, and needs the archive admin key on every target with a copy. It sets the hold to OFF with the archive admin key and checks it. The document is released only when every copy is.

Every set and release is logged in the document's **Legal Hold Log** (action, time, user, reason, result). A document under legal hold cannot be deleted, neither in the site nor through the deletion below.

### Deleting expired documents

The script report **Expired Documents** ("Abgelaufene Dokumente", roles Archive Admin and System Manager) lists documents that may be deleted:

- every copy is archived, and the retain-until date has passed on the document, on every copy, and (for contracts) also for the current contract end date,
- no legal hold on the document or any copy, and no retention extension in progress,
- contracts need a contract end date; an open-ended contract never qualifies,
- the version of every extension sidecar is recorded (sidecars written before v0.3 are recorded by a migration patch for the latest one only; documents with more than one older extension stay in place and show the reason).

An Archive Admin selects rows and clicks **Delete Selected**, or uses **Delete Expired Document** on the form. A preview lists every document with the reason it cannot be deleted, then a confirmation follows. The deletion runs in the background:

1. For every recorded version (file, attachments, meta, sidecars) on every target it sends a versioned HEAD and checks that the storage reports no legal hold and a retain-until date in the past. If any check fails, nothing is deleted.
2. It deletes exactly these versions with the archive admin key (`DeleteObject` with `VersionId`, i.e. `DeleteObjectVersion`), never without a version and never with `BypassGovernanceRetention`, and checks that each version is gone. Other versions under the same key stay. An AccessDenied or lock error means "not deletable yet": the copy keeps its state and shows the error.
3. Each copy is marked **Deleted** on its own. If a target fails, the document stays as it is and can be deleted again later; the targets that are done are skipped.
4. When every copy is deleted, the site removes the attached Files (main file and attachments), the attachment rows, the version history, comments, assignments, tags and the global search entry, and clears all content fields (title, number, file, keys, metadata, source fields, e-invoice data) and the reasons in the legal hold log. The Business Document remains as a stripped record with status **Deleted** and its copy rows (target, version IDs, retain-until, deleted at), which cannot be changed or deleted. Keeping the record keeps the document ID reserved and links such as `replaces` intact.
5. It writes a **Document Deletion Log** entry: document ID, type, own company, counterparty type and ID, retain-until, deleted at, deleted by, and no content. Log entries cannot be changed or deleted.

### Early deletion (GOVERNANCE only)

**Delete Early (GOVERNANCE only)** ("Vorzeitig löschen (nur GOVERNANCE)") lets an Archive Admin delete a document before its retention ends, for example after a justified erasure request. It is a separate form and list action:

- every copy must be locked in GOVERNANCE. Copies in COMPLIANCE are refused before any request. Before deleting, a versioned HEAD on every recorded version must report GOVERNANCE and no legal hold,
- the dialog shows "Aufbewahrungsfrist läuft noch bis <date>", needs a reason, and the user must type the document ID (or `LÖSCHEN` for several documents),
- it deletes the same recorded versions as above, with `BypassGovernanceRetention`. The archive admin key needs `s3:BypassGovernanceRetention` for this. Without it the storage answers AccessDenied, and the copy shows that the permission is missing,
- the deletion log records `early`, the reason and the overridden retain-until date.

### Export

**Export archive** ("Archiv exportieren", list view button and **Export** in the counterparty panel, role **Archive Admin** only) builds a self-contained package of archived documents, e.g. for a tax audit, a provider change or a leaving customer. The dialog filters by storage target, own company, document type, date range and counterparty; the counterparty panel prefills its counterparty. Each export is an **Archive Export** record ("EXP-…") with the scope, the user, the progress and the result, and stays as a log after its files expire.

The export runs on the long queue:

- Only one export runs at a time. Starting another one while an export is queued or running is refused.
- The selection uses the requesting user's permissions, so User Permissions on the Archive Admin limit the export.
- It includes documents with an archived copy on the chosen target whose versions are all recorded; others are counted as "Not on Storage Target".
- It reads every recorded version (file, attachments, `.meta.json`, extension sidecars) from storage with the archive key, never from the Frappe file, through `store.download`, the only function that calls `GetObject`. Four objects are fetched in parallel, each streamed to a temp file, retried up to three times.
- Each object's SHA-256 is checked against the recorded checksum (sidecars: against the stored `sha256` metadata). A mismatch or an unreadable object is listed in the record and in `manifest.json` and does not stop the export.
- Above **Export Part Size (MB)** (Archive Settings, default 2048, at most 4096) the export is split into parts, each a complete package. Before each part, it checks the free disk space for that part.
- Each ZIP (ZIP64; PDFs and images stored without compression) is uploaded with `s3storage.backend.insert_file` as a private File attached to the record, in parts and without loading it into memory. Downloads support `Range`, so they can resume.
- After **Keep Exports For (Days)** (default 7) a daily job deletes the files and sets the record to Expired. The same job marks exports that are still queued or running after 7 hours as Failed (the job timeout is 6 hours) and removes their files and temp directories.
- Export files can only be downloaded by users with the role Archive Admin, also by the user who started the export.

Each ZIP contains:

- `documents/<object key>`: the archived objects with their exact bytes,
- `manifest.json` and `manifest.csv`: scope, every document with its metadata (taken from the archived `.meta.json` or the newest sidecar), retention, lock mode, legal hold, and per object the key, version ID, SHA-256, size and check result,
- `previews/<object key>.html`: for `.eml` files a rendered, sanitized preview (HTML body or plain text) with a Content-Security-Policy that blocks scripts and remote content,
- `index.html`: an offline viewer in one file, without network requests. It lists all documents with search and filters (type, counterparty, own company, year, legal hold, problems), shows the metadata and retention trail, previews PDFs, images, XML and mail, and always links the original to open or download. Files that a browser could run (HTML, SVG, XSLT and similar) are only offered as a download. The embedded data escapes `<`, `>`, `&`, U+2028 and U+2029, so no document text can end the script block. Documents with a `thread_key` show their conversation in date order. The metadata block always shows the same fields with labels (empty ones as "–"), so the file starts at the same position for every document. Keyboard: j/k or ↑/↓ move through the list, Enter opens the preview, `/` focuses the search, Esc clears the search or closes, ←/→ switch attachments and `?` shows all shortcuts; none of them fire while typing. Under "Export Viewer" in Archive Settings you can set a brand name, a logo (PNG, JPEG, GIF or WebP up to 200 KB, embedded as a data URI) and a hex accent color, and turn off the "Made with docarchive" footer link. Without these the viewer stays neutral.

### Archive now

Users who may write a document see **Archive now** on its form when archiving is enabled and the document is complete. The list view has the same action for selected documents. Both show the retain-until date and the mode and ask for confirmation, then queue the same archive code as the nightly run, without the minimum age wait. A failed target is retried by the next nightly run.

### Commands

```bash
bench --site <site> docarchive-run --dry-run   # list what would be archived, write nothing
bench --site <site> docarchive-run             # archive now (needs Enabled), exit 1 on failures
bench --site <site> docarchive-smoke --target primary
bench --site <site> docarchive-sync-mode --target primary   # after switching objects to COMPLIANCE
bench --site <site> docarchive-extract --dry-run            # e-invoice data for existing documents
```

`docarchive-smoke` works without Enabled. It uploads a tiny `_smoke/` object with a 1-day GOVERNANCE lock, reads it back and checks its retention. Then it tries to delete that version with the target's credentials, once normally and once with `BypassGovernanceRetention`. Both must be refused with AccessDenied (or the provider's object-lock error), and a versioned HEAD must still find the object. It exits with status 1 if any check fails. Each run leaves one small object that expires after a day.

## Installation

```bash
bench get-app https://github.com/EffizienzAgent/s3storage --branch version-16
bench get-app https://github.com/EffizienzAgent/docarchive --branch version-16
bench --site <site> install-app docarchive
```

docarchive requires the [s3storage](https://github.com/EffizienzAgent/s3storage) app (v0.3.0 or later) for its S3 client and for storing large export files. s3storage keeps files on local disk until you configure its own bucket.

**Compatibility:** Frappe v16. The `version-16` branch always points to the latest release; tagged releases
and the changelog are on the [releases page](https://github.com/EffizienzAgent/docarchive/releases).

## Tests

```
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app docarchive
```

CI runs the suite on a Frappe v16 bench at the commits pinned in `.github/versions.env`. With `DOCARCHIVE_TEST_S3_ENDPOINT`, `DOCARCHIVE_TEST_S3_ACCESS_KEY` and `DOCARCHIVE_TEST_S3_SECRET_KEY` set, the tests also run against a real S3 server (CI uses MinIO with Object Lock buckets; the MinIO root key also serves as the archive admin key).

## Professional setup & support

Need help setting this up or integrating it into your Frappe stack? Write to oss@effizienzagent.de (English or German). German-speaking SMEs: see [mittelstandshub.de](https://mittelstandshub.de).

## License

MIT
