# Correspondence

docarchive can keep business letters, above all e-mails, in the format in which they were received or sent: the original MIME message (`.eml`). It decides nothing for you. Which mails are business letters, and how long they are kept, is your call (see [No compliance promise](../README.md#no-compliance-promise)).

## Account settings and modes

Archive Settings → **E-Mail Accounts** has one row per Frappe Email Account (System Manager only):

| Field | Default | Meaning |
|---|---|---|
| Email Account | – | The account. |
| Mode | Off | **Off**: nothing is captured. **Manual**: every mail is captured, a person archives it. **Automatic**: every mail is captured and every qualifying mail becomes a Business Document right away. |
| Outgoing Originals From | Frappe | **Frappe**: the original of a sent mail is rebuilt after sending. **Sent Folder**: it comes from the IMAP Sent folder through mailsync (see below). Only for IMAP accounts. |
| Archive Internal Mails | off | Whether mails where every address is internal qualify at all. |
| Contacts Count as Counterparty | off | Whether a Contact without a linked party (e.g. a private contact) may be set as counterparty automatically. |
| Keep Unarchived Originals (Days) | 90 | After this, captured originals that were not archived are deleted. At least 1. |
| Own Company | empty | Own company for this account's mails. Empty: the normal resolution. |
| Internal Domains | domain of the account's address | Comma-separated. |

Mail Originals keep the account's name as plain text, so an Email Account can be deleted once its row here is removed; its originals stay and expire as described under [Cleanup](#cleanup).

Capturing never makes receiving or sending fail: every capture runs in its own savepoint, and errors go to the Error Log ("docarchive mail capture") while the mail goes through.

Capturing is split so that slow file storage (e.g. S3) never stalls receiving, sending or mailsync's job. While the mail is handled, docarchive only records a **Mail Original** with status **Pending**: for a received mail it writes the raw bytes to a spool file in the site folder (`private/docarchive/mail-spool/<name>.eml`, folder mode 0700, files 0600); for a sent mail it only notes the Email Queue row. After the transaction commits, a job on the `long` queue (one per Mail Original, deduplicated) stores the `.eml` as a private File, fills in the headers and SHA-256, sets the status to **Stored**, deletes the spool file and, in Automatic mode, creates the Business Document. mailsync's hook never commits, and the send path renders nothing: the rebuild of a sent message happens in the job. The spool keeps megabytes out of Redis and out of a single database packet, but it **requires that web, worker and scheduler processes share the sites folder**. Frappe's usual setups do (`site_config.json` lives there), but a multi-pod setup without a shared volume does not; there every received original ends up Failed. Frappe's **Max File Size** (System Settings, 25 MB by default) applies to originals too: a larger mail cannot be stored and ends up Failed with that error.

If the job fails, the error is logged (Error Log "docarchive mail capture") and kept on the Mail Original, which stays **Pending** with its attempt count; the daily cleanup enqueues every pending original again. A missing spool file counts as an ordinary attempt. After 5 failed attempts, or at once when the bytes cannot come back (the Email Queue row was deleted, which Frappe does after the Email Queue retention in Log Settings, possibly earlier than 30 days; it can no longer be rebuilt; or the rebuild window has passed), the status becomes **Failed** and the Mail Original shows the reason. Pending and failed originals are never deleted by the age cleanup. Failed originals and originals pending for more than 24 hours show up in the **Archive Completeness** report as problem **Mail Original**, and the archive admins get one notification per day about them. `bench --site <site> execute docarchive.mail.originals.retry_failed` sets all failed originals back to pending and enqueues them. This cannot recover a sent mail rebuilt by Frappe once the one-hour rebuild window has passed, so such an original usually ends up Failed when its first store fails; an account that takes outgoing originals from the Sent Folder via mailsync does not have this limit; when the same mail is handed in again (e.g. read again by mailsync), a failed original takes the fresh bytes and is retried. Archiving a mail whose original is pending or failed is refused with that reason. Spool files without a pending or failed original are removed by the daily cleanup after a day.

- **Incoming**: every mail Frappe receives into a Communication (IMAP, POP3, Frappe Mail) keeps its raw message as a private **Mail Original** with its SHA-256. This includes a mail that is read again for an existing Communication without an original: the original is filled in. An existing original is never replaced.
- **Outgoing (Frappe)**: after a successful send, docarchive rebuilds the message that went to the first recipient from the Email Queue. This is the message Frappe built, not a byte copy of what went over the wire: print-format attachments are rendered again, and the copies of other recipients differ in tracking and unsubscribe links. The meta records this and the rebuild time (`rebuilt_at`, UTC). The rebuild runs in the background job and only within one hour of sending; later, print formats could render from a newer state of the document, so the original is marked Failed with "rebuild window passed" instead. Newsletters and queue rows with "redact message after send" are not captured.
- **Outgoing (Sent Folder via mailsync)**: if the mail server stores sent mails itself (e.g. Microsoft 365, Gmail), the copy in the IMAP Sent folder is closer to what was sent. With the mailsync app installed, docarchive handles mailsync's `mailsync_mail_read` hook and captures each mail with origin `mailsync`. mailsync calls this hook once per mail, when it first reads the mail's UID. Mails that are already in the Sent folder when mailsync runs for the first time are not handed in. Neither app imports the other; each works on its own.

## Automated and internal mails

A mail counts as automated if it has a `List-Unsubscribe` header, an `Auto-Submitted` header other than `no`, or `Precedence: bulk`, `list` or `junk`. A mail is internal if every address in From, To and Cc belongs to an internal domain. Automatic mode never archives automated mails, and internal mails only when **Archive Internal Mails** is on. Internal mails get the seeded type **Internal Correspondence** (6 years, no counterparty required), all others **Correspondence** (6 years, counterparty required). Both types are seeded once (on install, or by a patch on the first migrate after upgrading); a type of the same name is reused and flagged as correspondence if it has the seeded slug or no documents yet (otherwise it is left alone and the Error Log says so), and a type you delete is not created again. Archiving refuses mails while their type is missing. In manual mode a person can still archive an automated or internal mail after a warning.

## Archiving and discarding

- The Communication form has **Archive** (disabled with the reason when no original was captured, or while it is pending or failed) and, once archived, a link "Archived as …". The Communication list has **Archive** for selected mails and reports the mails it could not archive in one message. It needs read access to the Communication and create permission on Business Document, checked on the new document, so User Permissions on the own company apply (also to `.eml` uploads).
- Each mail becomes one Business Document per own company and Message-ID (without a Message-ID: per SHA-256). Archiving it again returns the existing document. The document gets the `.eml` as its file, the subject as title, the date the mail was received or sent, `source_doctype` = Communication, `source_system` = `email`, the account, and the correspondence fields: channel, direction, time, `thread_key` and the Message-ID headers. These are server-only for mails and locked once archiving starts.
- The counterparty comes from the Communication's links: Contacts with only internal addresses are dropped, a Contact stands for its linked Customer, Supplier etc. (or for itself, if **Contacts Count as Counterparty** is on and it has none). If exactly one candidate is left, it is set; otherwise the document waits in `documents_to_assign()`. The nightly run picks up links added later until archiving starts. Host `docarchive_assign` hooks still run.
- **Discard** (form button and list action, with confirmation) removes a mail document that has not started archiving, together with its captured original. It works for captured and uploaded mails, needs write permission and is refused for archived documents and documents marked for legal hold.
- Letters and faxes are uploaded like other documents with a type that has **Correspondence** set; the upload dialog then asks for channel, direction and time.

## Cleanup

A daily job deletes, per account, stored originals older than **Keep Unarchived Originals (Days)** that were not archived; pending and failed originals are kept (see above). This also applies to accounts set to **Off** (originals captured before) and to accounts whose row was removed, which use the default of 90 days. It also removes mail documents of that age whose archiving has not started and that still have no counterparty although their type requires one, together with their original, unless they are marked for legal hold. **Internal Correspondence** and other types without a required counterparty are not removed. So spam and private mail never reach WORM storage unless someone assigns them. Deleting a Communication deletes its unarchived original with it; an archived mail stays. If that fails, the error is logged, the original is only unlinked from the Communication, and the delete goes through.

## Viewer

**Show e-mail** on a correspondence document reads the archived `.eml` itself (the same bytes the archive run reads) and shows the headers, the origin and whether the checksum matches. The body is sanitized on the server and shown in an `<iframe sandbox>` without scripts and with a Content-Security-Policy. Remote images are blocked until you click **Load images**; images embedded in the mail (`cid:`) show right away, up to 5 MB per image and 20 MB per mail; larger ones show a placeholder. Each attachment can be downloaded, extracted from the `.eml` on the fly, or filed with **File as document**, which opens the upload dialog with the counterparty and date of the mail and sets `source_doctype` / `source_name` to the mail's document.

## Threads and gaps

**Show thread** (on the document and as a row action in the counterparty panel) lists the archived mails with the same `thread_key` and own company in time order, with direction, date and sender or recipient. Every Message-ID in a mail's `References` or `In-Reply-To` that is not archived shows up as a gap, in the order of the References chain. A gap is either **Archived in another thread** (a readable document of the same own company has this Message-ID, e.g. because its `thread_key` differs; the gap links to it), **Original available** (it can be archived from there), **In Frappe, no original** (e.g. received before capture was on; only if you can read that Communication) or **Not in Frappe** (only the Message-ID is known). A gap is information, not an error: the archive makes completeness checkable, it does not claim it.

## Closing gaps with an `.eml` upload

**Upload .eml** on a gap takes the mail as exported from a mail client. Its Message-ID must match the gap, otherwise the upload is refused. You pick the direction; own company and counterparty come from the thread. The `.eml` becomes a Mail Original without Communication, with origin `manual-upload`, and is archived through the same path as captured mails (same dedupe, fields and meta). The viewer and the meta show the origin, so it stays clear that a person supplied this original. The upload in the counterparty panel accepts `.eml` files the same way: it asks for the direction and files them for that counterparty.

## Host apps: `docarchive.mail.capture`

Apps that create Communications themselves can hand in the original bytes:

```python
from docarchive import mail

mail.capture(communication, raw, "my-app")  # Communication doc, bytes (or str), origin
```

The account settings come from the Communication's `email_account`. Without an account, or with an account that is off or not configured, nothing is captured. `capture` returns the Mail Original's name, or `None`; it never raises and does not commit. The original is pending until your transaction commits and the storage job has run (see above). A Communication that already has an original keeps it; one without an original gets it. Automatic archiving and cleanup apply as for Frappe's own mails, and the origin goes into the meta.

## Meta

For correspondence types the `.meta.json` gets a `correspondence` section (`null` for other types): channel, direction, time, `thread_key` and, for e-mail, a `mail` section with Message-ID, `In-Reply-To`, References, From, To, Cc, subject, the sender's `Date`, account and origin (and, for mails rebuilt after sending, the note on recipients' copies).

## Behaviour change: `source_name`

`source_name` on Business Document is now a Data field instead of a Dynamic Link. Archived documents therefore no longer block deleting their source record (for example a Communication). The document, its file and its archived copies stay.
