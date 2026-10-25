# Business correspondence archive (issue #6)

Status: draft for review. Date: 2026-10-25.

## Why

docarchive is a building block for businesses that run Frappe. They must keep business letters (Handels- und Geschäftsbriefe, mostly e-mails) unchanged for their retention period, and they want to prove later what was sent or received, when, between whom, with which attachments. Three situations matter:

- a tax advisor who wants to follow a case,
- a tax auditor (Betriebsprüfung) who checks completeness and immutability,
- a legal dispute with a counterparty, where the complete exchange must be shown.

Today e-mails only live as `Communication` records. They are neither immutable nor archived, and Frappe throws away the original message after parsing it.

### Legal background (research, not legal advice)

Checked against secondary sources (KPMG, DATEV, Bitkom, IHK) as of October 2026; the BMF text itself was not read.

- Business letters must be kept for 6 years, booking documents and invoices for 8 (since BEG IV, 2025), books and the process documentation (Verfahrensdokumentation) for 10. The period starts at the end of the year in which the letter was received or sent.
- Only mails that prepare, close, perform or undo a business deal are business letters. Newsletters, spam, private and purely internal mails are not. A mail that only transports an invoice is like an envelope: the attachment is what counts.
- Electronic letters must be kept in the format in which they were received (GoBD Rz. 131). So the original MIME message (`.eml`) is the archive object. A PDF is at most an extra rendering.
- The README's "no compliance promise" stays: docarchive gives the means, the customer decides what to keep.

## Goals

1. Capture the original MIME bytes of every incoming and outgoing mail of the configured e-mail accounts, at the moment Frappe receives or sends it.
2. Archive selected mails as Business Documents of a new type "Correspondence" with their own retention period, using the existing archive run, locks, key layout and storage targets.
3. Per e-mail account: a mode (off, manual, automatic), whether internal mails count, and how long unarchived originals are kept.
4. Link each archived mail to its counterparty, its Communication and its thread, and tell incoming from outgoing.
5. Show an archived mail readably from the archived bytes themselves, with its thread and its attachments.
6. Let other apps (e.g. mailsync) hand in original bytes for the Communications they create.

7. Record letters and faxes (uploaded scans) as correspondence with channel and direction.

## Non-goals

- Messenger channels such as WhatsApp. The neutral fields and the meta layout leave room for them; capture, original format and viewer would be their own feature.

- Mails that were in Frappe before the feature was enabled. If needed later, a journal mailbox import (route the old mailboxes into a mailbox that docarchive reads) is a separate feature.
- A PDF rendering of mails.
- Automatic classification of attachments (e.g. detecting invoices).
- Decrypting S/MIME or PGP mails.
- The other GoBD features from the research (legal hold, audit log, auditor access and export, integrity check, completeness report, e-invoice XML, deletion run, access restriction, process documentation). They get their own issues.

## Anti-goals

- Mail receipt or sending must never fail or slow down noticeably because capturing or archiving failed. Errors are logged; the mail goes through.
- Never archive newsletters, automated or (by default) internal mails in automatic mode.
- Never present a reconstructed message as an original. In this version there is no reconstruction at all.
- No change to Frappe core files and no monkeypatching of module-level functions.

## Constraints

- Frappe v16 (current: 16.53). `extend_doctype_class` is available, and several apps can extend the same doctype with it.
- Frappe does not keep the raw message. During receipt it exists only on `InboundMail.raw_message` (`frappe/email/receive.py`); `EmailAccount.receive()` calls `mail.process()`, which creates the Communication. When sending, `EmailQueue` builds the MIME bytes per recipient (`build_message(recipient)`, with per-recipient tracker and unsubscribe placeholders replaced) and hands them to SMTP. Email Queue rows are deleted after 30 days by default.
- Communications are linked automatically: `set_timeline_links` adds the Contacts found by e-mail address and the Contacts' dynamic links (Customer, Supplier, Lead, …) as `timeline_links`.
- Existing docarchive building blocks to reuse: `source_doctype` / `source_name`, the `docarchive_assign` hook, `counterparty_required` per type, `documents_to_assign`, `reassign()` in the nightly run, `min_age_hours`, the locks after `archive_started_at`, `content_sha256`, the `.meta.json` sidecar.

## Decisions

### Capture (decided with the user)

- Incoming: extend `Email Account` through `extend_doctype_class`. Each `InboundMail` that becomes a new Communication has its `raw_message` saved as a private File attached to that Communication, named `<message-id slug>.eml`, with its SHA-256. This covers IMAP, POP3 and Frappe Mail accounts.
- Outgoing: extend `Email Queue` the same way. After a successful send, the bytes that went to the first recipient are saved as a private `.eml` File on the Communication linked to the queue row. The meta records that per-recipient copies differ in tracking and unsubscribe links. (Builder's call where exactly to hook, as long as it is the sent bytes and not the template with placeholders.)
- Other apps: a public function `docarchive.mail.capture(communication, raw, origin)` stores the original for a Communication the app created. mailsync calls it for imported sent mails, which it already reads raw (`fetch_raw`). `origin` (e.g. `frappe-receive`, `frappe-send`, `mailsync`) goes into the meta. The account settings come from the Communication's `email_account` (mailsync sets it); a Communication without an account, or whose account is off or not configured, is not captured and `capture` returns `False`. Automatic archiving and cleanup apply as for Frappe's own mails. (My call.)
- Capture runs only for accounts whose mode is not "off".
- Capturing never raises into the receive or send path.

### Per-account settings (decided with the user)

A table in Archive Settings with one row per e-mail account (only System Manager sees it, like the rest of the settings):

| Field | Default | Meaning |
|---|---|---|
| Email Account | – | the account |
| Mode | Off | Off: nothing is captured. Manual: capture, archive on request. Automatic: capture and archive every qualifying mail. |
| Archive internal mails | off | whether mails where every address is internal qualify; only then are they archived at all |
| Contacts count as counterparty | off | whether a Contact without a linked party (e.g. a private contact) may be set as counterparty automatically. Off: such mails stay without counterparty and are archived only if a person assigns one |
| Keep unarchived originals (days) | 90 | after this, captured originals that were not archived are deleted |
| Outgoing originals from | Frappe | Frappe: rebuilt from the Email Queue after sending. Sent folder (mailsync): nothing is captured on send; mailsync reads the IMAP Sent folder and calls its own hook `mailsync_mail_read(communication, raw, account, folder, sent_or_received)` for every mail, also for Communications that already exist; docarchive registers a handler for it that calls `capture(communication, raw, "mailsync")`. Neither app imports the other, so each runs on its own. Only allowed for IMAP accounts. Worth it when the mail server stores sent SMTP mails itself (e.g. Microsoft 365, Gmail); if Frappe appends to the Sent folder itself, the copy is the same Frappe-built message. (Decided with the user.) |
| Own company | empty | own company for this account's mails; empty: the normal resolution |
| Internal domains | domain of the account's address | comma-separated |

### Automated mails (decided with the user)

A mail counts as automated if it has `List-Unsubscribe`, `Auto-Submitted` other than `no`, or `Precedence: bulk`, `list` or `junk`. Automatic mode skips it. In manual mode a person can still archive it after a warning (my call: a person decided on purpose).

### Archiving

- Manual: a button "Archive" on the Communication form and a bulk action in the Communication list. It needs read access to the Communication and create permission on Business Document. It creates the Business Document and attaches the captured `.eml` to it. Without a captured original the button is disabled with the reason ("no original captured").
- Automatic (decided with the user): right after capture, if the mail is not automated and (internal mails are on, or the mail is not internal), a Business Document is created. If the counterparty resolves, the nightly run archives it as usual. If not, it waits without a counterparty: a person has the keep period (default 90 days) to assign one, which can be followed by the existing "archive now" or the nightly run. When the keep period ends without a counterparty, the document is removed together with its original (see Cleanup). So spam and private mail never reach WORM storage unless someone assigns them.
- Discard (decided with the user): an action "Discard" on a correspondence document whose archiving has not started, also as a bulk action. It removes the document and its captured original. Document Managers may use it, through a server method, since they have no delete permission in general. Archived documents can never be discarded.
- The Business Document: type "Correspondence", `document_date` = date the mail was received or sent in Frappe (`communication_date`), `title` = subject, `source_doctype` = Communication, `source_name`, `source_system` = `email`, `source_account` = the account.
- Channel-neutral fields on Business Document (decided with the user, so letters, faxes and later messengers such as WhatsApp fit without a rebuild), in a "Correspondence" section: `correspondence_channel` (E-mail, Letter, Fax, Other), `correspondence_direction` (Incoming / Outgoing), `correspondence_at` (when it was received or sent), `thread_key`. Mail-specific fields next to them: `mail_message_id`, `mail_in_reply_to`, `mail_references`. All are locked after archiving starts. For documents whose source is a Communication they are server-only (direction from `sent_or_received`); for uploaded letters and faxes, channel, direction and date can be entered by the user, `thread_key` stays server-only.
- Business Document Type gets a flag "Correspondence". For such types, channel and direction are required before archiving (they join the existing missing-fields check), and the upload dialog asks for them. Both seeded correspondence types have the flag.
- `thread_key` (mail) = the first Message-ID in `References`, else `In-Reply-To`, else the mail's own Message-ID. Other channels set their own thread key later (e.g. a chat id); letters have none in this version.
- Dedupe: one Business Document per own company and Message-ID; for mails without a Message-ID, per own company and SHA-256. Archiving an already archived mail opens the existing document. (A mail sent from Frappe to an internal colleague whose inbox is also connected yields two captures with the same Message-ID; the first wins.)
- The seeded type "Correspondence": 6 years, counted from the document date, counterparty required, number not required. Editable like the other seeded types.
- Internal mails (when the account allows them) get a second seeded type "Internal Correspondence": 6 years, counterparty not required. (My call: internal mails have no external party, and a required counterparty would block them forever.)

### Counterparty (decided with the user, details my call)

- docarchive ships a built-in assignment step for documents whose source is a Communication: it reads the Communication's `timeline_links` and keeps those whose doctype is allowed as counterparty (`docarchive_counterparty_doctypes`). Contacts whose address is internal are dropped first. A Contact is a candidate only if it has no linked party of its own and the account's "Contacts count as counterparty" is on; otherwise its linked parties stand in for it (my call: Frappe always adds both the Contact and its Customer/Supplier, so counting both would never yield exactly one). Exactly one candidate left: it is set. None or several: it stays empty, and the document shows up in `documents_to_assign`, where a person picks one. Host `docarchive_assign` hooks still run and can override.
- `reassign()` in the nightly run already retries documents without a counterparty; it must also pick up documents with `source_doctype` = Communication, so links added by hand later are taken over until archiving starts. `min_age_hours` gives people time to link before the run.
- The Communication form shows a link to its Business Document, so the relation is visible both ways.

### Cleanup (decided with the user)

A daily job deletes captured `.eml` Files on Communications that are older than the account's keep period and have no Business Document. It also removes correspondence documents older than the keep period whose archiving has not started and that still have no counterparty, together with their original. Files used by a Business Document are never touched (the existing guards also protect them). Deleting a Communication deletes its unarchived capture with it.

### Meta

The `.meta.json` gets a `correspondence` section for correspondence types (`null` otherwise): `channel`, `direction`, `at`, `thread_key`, and a channel-specific sub-section. For e-mail that is `mail`: `message_id`, `in_reply_to`, `references`, `from`, `to`, `cc`, `subject`, `date_header` (the sender's `Date`), `account`, `origin`, and for outgoing mails a note that recipients' copies differ in tracking links. A later messenger channel adds its own sub-section next to `mail`. (Whether this bumps the format version is the builder's call.)

### Viewer (decided with the user, details my call)

- A whitelisted method parses the `.eml` of a Business Document with Python's `email` package (no new dependency) and returns headers, the text or sanitized HTML body, inline images and the attachment list. It needs read permission on the Business Document. It reads the bytes the same way the archive run does (`content.read_bytes`), and reports whether their SHA-256 matches `content_sha256`.
- A dialog on the Business Document shows the headers, then the body in a sandboxed iframe without scripts. Remote images are blocked until the user clicks "load images"; `cid:` images come from the message itself.
- Each attachment can be downloaded (extracted from the `.eml` on the fly) and has an action "File as document", which opens the existing upload dialog prefilled with counterparty and date and sets `source_doctype` / `source_name` to the correspondence document.

### Thread view

On a correspondence document and in the counterparty panel: all archived mails with the same `thread_key` and own company, in time order, with direction. Every Message-ID in an archived mail's `References` (and `In-Reply-To`) that is not archived is shown as a gap, in the order of the References chain (decided with the user). A gap has one of three states: the original is still captured (offers "Archive"), the mail is in Frappe without an original (shows why), or the mail is unknown to Frappe (shows only the Message-ID). A gap is information, not an error: the archive makes completeness checkable, it does not claim it.

### Closing gaps with an uploaded `.eml` (decided with the user)

A gap (and the Correspondence upload in general) can be closed by uploading the mail as `.eml`, e.g. exported from a mail client. docarchive parses it; when uploaded for a gap, its Message-ID must match the gap, otherwise the upload is refused. The user picks the direction and the own company defaults as usual. It becomes a Mail Original without Communication, origin `manual-upload`, and is archived through the same path as captured mails (same dedupe, fields and meta). The meta and the viewer show the origin, so it stays clear that this original came from a person, not from the mail flow.

## Left to the builder

- Module layout, names of the new doctypes and methods, translations (the app ships German translations).
- Exact hook points inside `Email Account` and `Email Queue`, as long as the constraints above hold and there is a test that a receive or send still succeeds when capture fails.
- How the bulk action reports mails without an original.
- How internal mails are detected from the addresses and domains.
- Whether the counterparty panel lists correspondence together with other documents or in its own tab.
- README section for the feature, including how a host app calls `docarchive.mail.capture`.
