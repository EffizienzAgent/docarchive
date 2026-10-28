# Archiving

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

## Object keys

```
<own-company>/<year>/<type-slug>/<counterparty-id>/<document-id>_<date>_<number or title>.<ext>
<same key without extension>__att-<n>-<title>.<ext>
<same key>.meta.json
```

For example `acme-gmbh/2026/outgoing-invoice/cust-0042/DOC-2026-00017_2026-03-15_INV-2026-017.pdf` or, for a mail without a number, `acme-gmbh/2026/correspondence/cust-0042/DOC-2026-00014_2026-06-04_re-anfrage-zusatzlager-juli.eml`. Files sort by document ID, which also keeps every key unique. The document ID and the number keep their case; other characters than letters, digits, `.` and `-` become a hyphen. Without a number the title is used, lowercased, transliterated and cut to 60 characters; without either the name is just ID and date. The folders are lowercased and transliterated, and a document without a counterparty goes to `ohne-gegenpartei`. An attachment goes next to the main file, numbered in table order and named after its title (or its role), e.g. `…_INV-2026-017__att-1-timesheet-march.pdf`. The key is computed once at archive start from fields that are locked from then on and the title at that moment, and stored with the attachment keys, so a retry always writes the same keys. Documents archived under the older scheme keep their keys (`<date>_<number>_<document-id>`); everything that works on archived objects uses the stored keys.

The `.meta.json` holds the document data (ID, title, type, date, number, contract end, own company), the counterparty, the main file (`file`), every file in `files` (main file and attachments, each with key, name, role, SHA-256 and size, so the set is provable), the source fields (including the optional source record `source_doctype` / `source_name`), and the retention (counts from, start date, years, retain-until date, mode). It is frozen at archive start (`archive_meta`), so retries write the same bytes.

## Retention

`retain_until` is January 2, 00:00 UTC, of the year after (start year + retention years). The extra day covers the end of the year in every time zone. The start is the document date, or the contract end date for types whose retention counts from it. Years come from the Archive Settings rule for own company and type, else the type's default. Without either, the document fails with an error and nothing is written.

## Contract end and retention extension

After archiving starts, the contract end date can be set for the first time or moved later, never earlier. Each night, after archiving, the run recomputes `retain_until` for archived documents whose type counts from the contract end, using the retention years stored at archive start. When the new date is later than a copy's date, it extends the retention of the main file, every attachment and the `.meta.json` on that target with `PutObjectRetention`, in the same mode. Each copy records its own date. The document's `retain_until` moves only when every enabled target has the new date, and a failed target is retried the next night. A shorter date never leads to a request.

Each extension also writes a sidecar `<key>.meta.<n>.json` (n = 2, 3, …) to every target, locked with the new date and the copy's mode. It holds the metadata with the new retention, the extension (previous and new date, contract end, time) and the earlier extensions, so the newest sidecar carries the whole trail. The original `.meta.json` is never rewritten: Object Lock versions are immutable, and a second version under the same key would hide the first. The sidecar content is frozen before the first write, so a retry writes the same bytes, and a contract end that moves again in the meantime leads to the next extension. Before extending, the run checks the SHA-256 of the exact object version it recorded.

## Manual extension

Document managers can extend the retention of archived documents by hand: "Extend retention" on the form (add N years or pick a date), "Extend retention" in the Business Document list and "Extend all documents of this counterparty" in the panel (at least N years from the retention start, so each document gets the later of its current date and start + N years, or a fixed date). Bulk extensions show a preview with the current and new date per document and the skipped ones, then run as a background job with progress and a summary.

A picked date counts inclusively: the copies stay locked until the end of that day (00:00 UTC the next day). The server refuses any date that is not later than every archived copy's current date or more than 100 years ahead, refuses documents with a copy on a target that is no longer enabled and deleted documents, and never shortens; the document's `retain_until` never moves earlier. A manual extension uses the same path as the contract end extension above: `extend_locked` per copy, a locked `.meta.<n>.json` sidecar, per-target status, and the nightly run retries targets that failed. The extension entry also records who extended, when and why (optional reason). While one extension is still running on a document, a new one is refused. The endpoints in `docarchive.extension` are POST only and check the manager role and write access.

## Retention overview

The Business Document list and the counterparty panel show `retain_until`; the panel filters and sorts by it. The report "Retention Overview" lists archived documents with a chart by retain-until year and filters for type, own company, counterparty, lock mode, "ends within N months" and failed copies. The number card "Retention ends in 90 days" and `docarchive.extension.count_ending(days)` give hosts a count to show.

## Object Lock writes

`docarchive.run.locked_objects(doc, copy)` lists every locked object version of a copy as `(key, version_id, sha256)`: the main file, each attachment and the `.meta.json` (extension sidecars are recorded separately, in `sidecar_versions`). Code that acts on all objects of a document, such as the retention extension and the mode sync, goes through it. `run.copy_objects(copy, doc)` adds the sidecars; legal holds and deletion use it, so they cover the main file, every attachment, the `.meta.json` and every sidecar.

`docarchive.store.put_locked(target, key, body, mode, retain_until)` is the only code that calls `PutObject`. Before any request it checks that the mode is `GOVERNANCE` or `COMPLIANCE` and that `retain_until` is a timezone-aware datetime in the future. It always sends `ObjectLockMode`, `ObjectLockRetainUntilDate`, `ContentMD5` and the SHA-256 as object metadata. `docarchive.store.extend_locked` is the only code that calls `PutObjectRetention`; see the retention extension above. A test parses the app's source: each method of each client (archive key `_client`, archive admin key `_admin_client`) may be used in exactly one function of `store.py`. The archive key never calls `PutObjectLegalHold`; `put_locked` can send `ObjectLockLegalHoldStatus=ON` at upload, and `smoke_delete` is its only delete. With the archive admin key: `_admin_hold` changes holds and is only called by `set_hold` and `release_hold`, and `_admin_delete` deletes a version and is only called by `delete_expired` and `delete_early`. The admin client may not be referenced anywhere else, so it cannot be aliased into another function. `BypassGovernanceRetention` is only allowed in `smoke_delete` and `delete_early`. Copy, multipart, presigned URL and policy calls are banned, and only `store.py` may import boto3, botocore or `s3storage.client` or touch a client.

The lock mode comes from the storage target and is recorded per copy. Switch a target to COMPLIANCE once your trial is done. New documents then use COMPLIANCE. Existing objects are switched outside the app with the offline admin key (see [docs/hetzner-example.md](hetzner-example.md)). Then run `bench --site <site> docarchive-sync-mode --target <name>`: it reads the retention of every recorded object version (main file, attachments, `.meta.json`) of every archived copy on that target. It only records an upgrade from GOVERNANCE to COMPLIANCE when all of them report COMPLIANCE, never a downgrade. Documents with any other difference are listed as mismatched, errors are listed per document, and the command exits with status 1 if there are any.

## Archive now

Users who may write a document see **Archive now** on its form when archiving is enabled and the document is complete. The list view has the same action for selected documents. Both show the retain-until date and the mode and ask for confirmation, then queue the same archive code as the nightly run, without the minimum age wait. A failed target is retried by the next nightly run.

## Mail states and suggestion

Every e-mail of an account in Manual or Automatic mode shows where it stands (unfiled mails of accounts that are Off or not listed in Archive Settings show nothing; filed and archived mails always keep their state). The state is a colored bar on the Communication form, a badge next to each mail in any activity timeline (on any form with a mail thread) and a badge in the Communication list.

| State | Color | Meaning |
|---|---|---|
| Should be archived | yellow | Not archived, and the suggestion found a signal (see below). |
| Not archived | gray | Not archived, no signal. Automatic and internal mails show only a quiet note. |
| Ignored | gray | Marked as not to be archived. "Undo" or "Archive" bring it back. |
| Filed | blue | Archive was used, the Business Document exists but is not sealed yet. Also covers a run in progress. A failed run shows red ("Failed"). |
| Archived | green | The document is archived (or deleted after retention) and shows its retain-until date. |

The bar of a not archived mail names the rule next to the actions "Archive" and "Don't archive" and says when the stored original is removed (the account's keep days). The rule is the text under **Archive Settings → Archiving Rule**; when empty, the default applies: archive when the mail prepares, closes, performs or undoes a business deal (offer, acceptance, order, contract or change, binding commitment, invoice, credit note, dunning, cancellation, withdrawal, complaint with consequences), not notifications, newsletters, scheduling or support without contractual consequences. **When in doubt, archive.**

The suggestion is only a hint, nothing is archived because of it. A mail is suggested when its subject contains a keyword at the start of a word (so "Auftragsbestätigung" matches "Auftrag") or it has a PDF or XML attachment. The bar shows which signals applied. Keywords come from **Archive Settings → Suggestion Keywords**, one per line; when empty, a default list of German and English terms (offer, order, contract, invoice, credit note, reminder, cancellation and similar) applies. Automatic and internal mails are never suggested.

"Archive" only files the mail as a Business Document. It is sealed by the nightly run once it has not changed for **Minimum Age (Hours)** (default 24), so the next run after that, and in practice between 24 and 48 hours after filing. The bar of a filed mail shows the earliest date it is sealed and offers **Discard** until then (for users who may write the document): it removes the document again and returns the mail to "Not archived", or to "Should be archived" when the suggestion applies. After the run starts, the document is locked and cannot be discarded.
