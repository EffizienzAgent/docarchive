# Legal hold and deletion

## Legal hold

**Set Legal Hold When Archiving** ("Legal Hold beim Festschreiben setzen") is a checkbox on the document and the `legal_hold` field of the upload API. It can be changed until archiving starts. When it is set, the archive run uploads every object (file, attachments, `.meta.json`, and sidecars written later) with `ObjectLockLegalHoldStatus=ON` and checks the hold with the versioned HEAD after the upload. This works with the archive key alone, so it is the only hold option without an archive admin key.

**Set Legal Hold** (form, and list action for selected documents) puts an already archived document on hold. It needs a reason and write permission on the document (or the Archive Admin role), and the archive admin key on every target with a copy; without an archive admin key the action is hidden and refused. It sets the hold to ON on the recorded version of the file, every attachment, the `.meta.json` and every extension sidecar, on every target, with the archive admin key, and reads it back with a versioned HEAD. Each copy records its own state (`Off`, `On`, `Failed`). The document counts as held (`legal_hold`, shown and filterable in the list) as soon as one copy holds. Copies added later and sidecars written by a later extension are uploaded with the hold while the document is held. Setting it again retries the copies that failed.

**Release Legal Hold** is only for the role **Archive Admin**, needs a reason and a confirmation, and needs the archive admin key on every target with a copy. It sets the hold to OFF with the archive admin key and checks it. The document is released only when every copy is.

Every set and release is logged in the document's **Legal Hold Log** (action, time, user, reason, result). A document under legal hold cannot be deleted, neither in the site nor through the deletion below.

## Deleting expired documents

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

## Early deletion (GOVERNANCE only)

**Delete Early (GOVERNANCE only)** ("Vorzeitig löschen (nur GOVERNANCE)") lets an Archive Admin delete a document before its retention ends, for example after a justified erasure request. It is a separate form and list action:

- every copy must be locked in GOVERNANCE. Copies in COMPLIANCE are refused before any request. Before deleting, a versioned HEAD on every recorded version must report GOVERNANCE and no legal hold,
- the dialog shows "Aufbewahrungsfrist läuft noch bis <date>", needs a reason, and the user must type the document ID (or `LÖSCHEN` for several documents),
- it deletes the same recorded versions as above, with `BypassGovernanceRetention`. The archive admin key needs `s3:BypassGovernanceRetention` for this. Without it the storage answers AccessDenied, and the copy shows that the permission is missing,
- the deletion log records `early`, the reason and the overridden retain-until date.
