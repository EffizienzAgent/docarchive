A WORM document archive app for the Frappe Framework

# docarchive

docarchive files business documents such as invoices, contracts and bank statements in a Frappe site. It is meant to copy each one, unchanged, into write-once (WORM) object storage for its retention period.

## Status

This version has the document model, the locks and the storage configuration. The nightly copy to Object Lock storage comes in a later release. Until then, nothing leaves the site.

## No compliance promise

docarchive is a technical tool. It does not make you compliant with GoBD, the GDPR or any other law or standard, and it gives no legal or tax advice. Retention periods, procedures and documentation are your responsibility. Check them with your tax advisor or auditor.

## How it works

- **Business Document**: one record per document, with a private file, a type, a document date, a number, an own company and a counterparty. The counterparty is a Dynamic Link (`counterparty_type` + `counterparty`), so it can point to any doctype the host app allows.
- **Checksums**: when a file is attached, the app computes `content_sha256` from the raw file bytes. The archive run compares this value with the bytes it reads back from storage.
- **Object Lock**: each archived object gets its own retention mode (GOVERNANCE or COMPLIANCE) and retain-until date. The app has no code path that deletes from an archive bucket. Object Lock only lets retention grow, never shrink.
- **Retention**: the retention period ends on January 1 after the document year plus the retention years. For contracts it can count from the contract end date instead. Years come from a rule per own company and document type in Archive Settings, or from the type's default. The seeded types (Outgoing Invoice, Incoming Invoice, Contract, Bank Statement) ship with editable defaults. Check them for your jurisdiction.
- **Locking**: once archiving starts (`archive_started_at`), the fields that make up the object key and the source fields can no longer change: own company, type, date, number, counterparty, file and `source_*`. Deleting the document is refused. Its file cannot be deleted, moved, made public or detached. A counterparty linked from an archived document cannot be renamed or merged.
- **Server-only fields**: the checksum, the archive status, keys and retention fields, and the `source_*` fields are restored on every save, unless server code sets `doc.flags.archive_write`.
- **Access**: files are always private and attached to their Business Document, so Frappe's File permission follows the document permission. A user who sets or changes a counterparty needs read permission on it. Queries go through `frappe.get_list`.

## Roles

The app creates the role **Document Manager** (read, write and create documents; no delete). System Manager configures everything. To let a host role in, add it to **Archive Settings → Manager Roles**. The app then grants that role the same rights through Custom DocPerm. On every save of the settings and on every migrate, it removes custom permission rows on these doctypes for roles that are not in the list.

Custom DocPerm replaces the standard permissions of a doctype. If you edit permissions on these doctypes by hand, keep the manager rows.

## Storage targets

Archive Settings has a **Storage Targets** table with one row per bucket (usually two, in two regions). Each row has a name, an enabled flag and a lock mode (GOVERNANCE or COMPLIANCE). Endpoint, region and bucket come either:

- from the site config (`<prefix>_endpoint`, `<prefix>_region`, `<prefix>_bucket`), with the credentials in two named site config keys, or
- from fields on the row, with the credentials in Password fields, which Frappe stores encrypted.

Credentials are never stored in plain text. Endpoints must use https and bucket names must be valid, for site config values once they are set too. Two targets cannot point to the same endpoint and bucket. The archive can only be enabled with at least one enabled target, and only when all its site config keys are set.

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

- With the `s3storage` app, docarchive registers `docarchive.guards.protect_file` under `s3storage_before_file_delete`.
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

## Install

```
bench get-app https://github.com/EffizienzAgent/docarchive
bench --site <site> install-app docarchive
```

Requires Frappe v16.

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
