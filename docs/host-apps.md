# Host apps

## Host app hooks

Add these to your app's `hooks.py`:

| Hook | Called with | Purpose |
|---|---|---|
| `docarchive_assign` | `doc` (Business Document, in `validate`, before archiving starts) | Fill `own_company`, `counterparty_type` and `counterparty`, e.g. from `source_system`, `source_account`, `source_party_type` and `source_party_id` (set by server code with `flags.archive_write`). Values the user set are kept. Do not save other documents here. |
| `docarchive_counterparty_doctypes` | – | List of doctypes allowed as counterparty. Empty: any doctype that is not a child table or a single. |
| `docarchive_read_file` | `file_url` | Return the raw bytes of a file, or `None`. docarchive reads files in s3storage's bucket and in the site folder itself; use this hook for files stored elsewhere. Hooks are asked first and the last hook wins. |
| `docarchive_einvoice_extractors` | `data`, `file_name` | Return e-invoice metadata or `None`. See [E-invoices](e-invoices.md#e-invoices). |
| `docarchive_mail_changed` | `action` (`"archived"`, `"ignored"` or `"unignored"`), `originals` (Mail Original names), `communications` (their Communications), as keyword arguments | Called after the transaction commits when mails were archived (manually, in bulk or automatically), ignored or unignored, e.g. to refresh a badge. Exceptions are logged ("docarchive mail changed hook") and never break the action. Not called when nothing changed. |

Own company resolution: an explicit value, then the `docarchive_assign` hooks, then the successor company by date, then the default company from Archive Settings. While a document has no date, the successor and default companies are not applied: own company stays empty and is listed as missing until the date is set.

File deletion runs inside the File controller, before any `doc_events` hook. So the delete guard also has to run there:

- With the `s3storage` app, docarchive registers `docarchive.guards.protect_file` under `s3storage_before_file_delete`, and `docarchive.guards.protect_object` under `s3storage_before_object_delete` and `s3storage_before_object_write`, which s3storage calls as `fn(file_url=..., key=...)` before it deletes or overwrites an object. So deletes and overwrites of an archived object (for example through `optimize_file`) are refused.
- If your app overrides the File class itself, call `docarchive.guards.protect_file(self)` at the start of `on_trash`, before `super().on_trash()`.
- Without either, deletes by users are still refused through `has_permission`. But a server-side delete with `ignore_permissions` would remove the bytes before the guard runs.

## Counterparty panel

The app's JS bundle offers a panel for any form: the record's documents with search, filters (type, year, status) and sorting, row actions (preview, open in a new tab, copy link, show thread for mails, archive now) and an upload with drag and drop. `.eml` files dropped there are filed as e-mails for the counterparty (see [Correspondence](correspondence.md#correspondence)). The upload dialog has one row per file with title, type and date. A date in the file name (`2026-08-05`, `2026_08_05`, `20260805` or `05.08.2026`) becomes the document date and is left out of the title. Uploads without a date take it from an e-invoice or the file name on the server too; otherwise the document stays without a date and is listed as missing it. With several files, the dialog can file them as one document: the first row becomes the main file, the others its attachments.

```js
frappe.ui.form.on("Customer", {
	refresh(frm) {
		docarchive.documents_panel(frm, "documents_html", "documents_section");
	},
});
```

The third argument may also be a Tab Break, so the documents get their own tab on the form (the demo data does this on Contact). `docarchive.render_documents($wrapper, { counterparty_type, counterparty })` and `docarchive.upload_documents({ counterparty_type, counterparty, files, on_saved, defaults })` are available for custom layouts (`defaults` may hold `document_date`, `source_doctype` and `source_name`). `docarchive.show_mail(name)` and `docarchive.show_thread(name)` open the mail viewer and the thread. Set `docarchive.file_url = (file_url) => url` to open files in your own viewer.

Rows the user can act on have a checkbox. A bar above the list then offers "Archive now", "Extend term", "Legal Hold" and, for Archive Admins, "Export" for the selected rows; rows without any of these show a disabled checkbox with the reason. Clicking a row opens a preview pane on the right (an overlay on narrow screens) with a fixed header (the same labelled fields for every document, empty ones as "–") and the main file and attachments as tabs. Up/Down or j/k move through the list, Esc closes, and the pane can be resized. PDFs and images show inline, other files as a download. HTML is never rendered by docarchive itself: set `docarchive.html_preview = (file_url, title) => Promise<HTMLIFrameElement>` to return a sandboxed frame from your app, otherwise the pane links to `docarchive.file_url`.

## Triage

`docarchive.triage(names, { start, on_close })` opens a dialog that walks through documents one by one: the fields and what is still missing on the left, and the file on the right (PDF and images inline, other types as a download). "Save and next" (Ctrl/Cmd+Enter), "Skip", "Back" and Alt+Left/Right move through the list; Esc closes. "Open document" opens the form in a new tab, so the dialog and unsaved input stay; documents that are not archived yet can be deleted from the dialog. Each save is a normal `doc.save()` with permission checks, and validation errors stay inline. `docarchive.triage_all()` opens it on every document that still needs work. The Business Document list has "Assign" for selected documents and "Assign all".

## Mails to archive

For accounts in Manual mode (see [Correspondence](correspondence.md#mails-to-archive)):

- `docarchive.mail.candidates(limit=50, expiring_within_days=None)` (whitelisted): stored, unarchived, not ignored originals, ordered by `expires_at` ascending. Each item has `name` (Mail Original), `message_id`, `subject`, `from`, `to`, `date`, `account`, `communication`, `expires_at` (creation + the account's keep days; the daily cleanup deletes the original on its first run after that), `automated`, `internal` and `suggested_counterparty` (`{counterparty_type, counterparty, title}` if archiving would set exactly one counterparty, else `None`; nothing is written). `limit` is capped at 200. `expiring_within_days` keeps only items expiring within that many days from now (already expired ones that the cleanup has not removed yet included).
- `docarchive.mail.count_candidates(expiring_within_days=None)` (whitelisted): the number of those items, for badges.
- `docarchive.mail.ignore(names)` / `docarchive.mail.unignore(names)` (whitelisted, POST): Mail Original names; return `{"ignored": [...]}` / `{"unignored": [...]}` with the originals that changed.
- `docarchive.mail.archive_originals(names)` (whitelisted, POST): archives Mail Originals, returns `{"archived": {original: business_document}, "refused": {original: reason}}`. `docarchive.mail.archiving.archive_communications(names)` does the same keyed by Communication. In the desk, `docarchive.archive_communications(communications, on_done)` asks first when mails look automated or internal.

All of them need create permission on Business Document (without it `candidates` is empty and the count is 0) and only cover mails whose Communication the user can read in a list (Frappe's Communication rules: System Manager and Super Email User see all, others the mails of their own email accounts). `ignore`, `unignore` and `archive_originals` refuse the whole call if one original is not readable.

A "Heute" section with the mails expiring within 3 days:

```python
# hooks.py
docarchive_mail_changed = ["myapp.today.mail_changed"]
```

```python
# myapp/today.py
import frappe
from docarchive import mail


@frappe.whitelist()
def mails_to_archive():
	return {"count": mail.count_candidates(expiring_within_days=3), "items": mail.candidates(10, 3)}


def mail_changed(action, originals, communications):
	frappe.publish_realtime("myapp_today_changed", {"action": action}, after_commit=False)
```

```js
frappe.xcall("myapp.today.mails_to_archive").then(({ count, items }) => {
	// show `count` as a badge; per item: subject, from, expires_at, suggested_counterparty?.title,
	// a hint if item.automated or item.internal, and buttons:
	//   docarchive.archive_communications([item.communication], refresh)
	//   frappe.xcall("docarchive.mail.ignore", { names: [item.name] }).then(refresh)
});
frappe.realtime.on("myapp_today_changed", refresh);
```

## Dashboard helpers

- `docarchive.queries.documents_to_assign()`: documents that are not archived and still lack a file, a date or a field their type requires, or whose archiving failed. Newest first, each with a `missing` list.
- `docarchive.queries.count_documents_to_assign()` (whitelisted): the number of those documents.
- `docarchive.expiry.due_count()` (whitelisted): the number of documents due for deletion (the rows of **Expired Documents**). The standard number card **Documents Due for Deletion** shows it.
- The document list (`counterparty_documents`) returns `legal_hold`, so panels can show the hold. Deleted documents are left out.
