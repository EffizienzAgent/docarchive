# Audit log

The **Archive Audit Log** ("Archivprotokoll") records state-changing archive events: one entry per trigger, not per document. A nightly run, an "Archive now" job, a legal hold set or release, a retention extension (single or bulk), a deletion run and an export each give one entry with the user, the time, the action and the documents it touched, each with an outcome (e.g. `archived`, `failed`, `held`, `deleted`, `exported`) and, for failures, a reason code (e.g. `checksum`, `storage:AccessDenied`). Changes to archive-relevant settings (Archive Settings, Business Document Types) and to the locked fields of a document before archiving are logged with the names of the changed fields. Views and downloads are not logged. A trigger that changes nothing (e.g. a refused hold) leaves no entry.

Entries hold no free text and no personal data, because they cannot be changed: reasons stay in the document's legal hold log and in the deletion log, changed values in the document's version history.

Each entry is stored as its own locked object `_audit/<yyyy>/<mm>/<utc-timestamp>-<id>.json` on every enabled storage target, with the target's lock mode, until January 1 after the entry year plus **Lock Audit Log For (Years)** (Archive Settings, default 10). Actions that write to storage (archive run, archive now, legal hold, extension, deletion, export) write their entry in the same operation, right after its database commit, so a rolled-back action leaves no object behind. A batch job that aborts (error or timeout) still logs the documents it already processed, marked `aborted`. Other entries are written by a background job right after the commit. Nothing is written while archiving is disabled. The database keeps a copy of every entry for the desk and as a retry buffer: an entry that a target could not take stays **Pending**, and the nightly run writes what is missing, also to a target added later.

After archiving, the nightly run compares the database with every bucket: entries changed in the database, objects missing in a bucket, objects with different content, objects in a bucket without a database entry, and entries not written within 24 hours. Any mismatch sends one bell notification per day to the Archive Admins (System Managers if there are none). Entries whose lock has ended are skipped, so a bucket lifecycle rule may remove them (see [the lifecycle rule](hetzner-example.md#7-lifecycle-rule-for-the-audit-log)). docarchive itself never deletes audit objects.

Changes to the locked fields of documents before archiving are logged when they are saved. Further changes in the same transaction by the same user extend that entry, as long as it is not stored yet: a save, an API call or a job step that changes many documents gives one `document.changed` entry, with the names of the changed fields per document, not one entry and one locked object per document.

## Retention and personal data

Each entry names the user who triggered it (the Frappe user ID, usually an e-mail address) and the IDs of the documents it touched. A daily job blanks the database copy of every entry whose lock has ended, also while archiving is disabled: user, reference, details, stored content and document rows are removed, and the entry keeps only its time, action and object key with the status **Expired**. The locked objects stay in the buckets until a lifecycle rule removes them (see [the lifecycle rule](hetzner-example.md#7-lifecycle-rule-for-the-audit-log)). The blanked row stays, without personal data, so the nightly comparison still knows the object. For your record of processing activities (GDPR Art. 30): purpose is the traceability of archive events, data are user IDs, times and document IDs, and the storage period is **Lock Audit Log For (Years)** plus the delay of your lifecycle rule. Entries cannot be corrected or erased earlier, because they are locked.

## Access

Archive Admins and System Managers can read the log (list **Archive Audit Log**); nobody can create, change or delete an entry in the desk. Each entry links its documents. The Business Document form shows the entries of the document in its **Audit Log** section to everyone who may read the document.

## API for other apps

Other apps can add entries:

```python
from docarchive import audit

audit.log(
	"mail.discarded",                      # short dotted action
	"Communication", "COMM-0001",          # optional: the record that triggered the entry
	details={"count": 2},                  # optional: JSON, no free text or personal data
	documents=[("Business Document", "DOC-2026-00017", "discarded")],  # (doctype, name, outcome[, reason])
	write=False,                           # True if the caller writes to storage in the same operation
)
```
