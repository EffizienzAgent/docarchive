# Faster, more visible mail archiving

Status: draft for review. Date: 2026-10-28.

## Why

Today a user has to open each Communication and click a plain toolbar button "Archive". Two things get in the way:

- **The decision, not the click.** Users are often unsure whether a mail belongs in the archive at all. Sometimes it only becomes clear later, but in Manual mode the original expires after `keep_days` (default 90), so "later" has a limit.
- **Visibility.** Users notice mails in places where archiving is not offered or not obvious: the activity timeline of an Organization or Contact, the Communication list, the CRM "Heute" view (an unlabeled icon today).

The goal is that, wherever a mail shows up, the user sees whether it is archived, whether it probably should be, and can archive it right there, guided by a short rule.

## The rule

Shown as guidance in the form's headline bar (short form) and as tooltip on the archive actions in timeline and list. Default text (English source, translated like all strings):

> Archive when the mail prepares, closes, performs or undoes a business deal: offer, acceptance, order, contract or contract change, binding commitment, invoice, credit note, dunning, cancellation, withdrawal, complaint with consequences. Not: notifications, newsletters, scheduling, support without contractual consequences. When in doubt, archive.

"When in doubt, archive" is the user's decision: a legitimate interest outweighs the cost of keeping a mail too many, and storage is secure.

The text is a generic default (based on § 257 HGB / GoBD business letters, not legal advice) and can be overridden per installation in Archive Settings (new long-text field, empty = default).

## The suggestion

A mail is **suggested** ("should be archived") when all of these hold:

- it has a Mail Original that is not archived and not ignored (`Mail Original.ignored`),
- it is not automated and not internal,
- its account is in Manual mode (Automatic mode archives by itself; accounts without docarchive mode show nothing),
- and at least one signal matches:
  - the subject contains a configured keyword (case-insensitive, word match). Default list (German and English): Angebot, Auftrag, Bestellung, Vertrag, Rechnung, Gutschrift, Mahnung, Kündigung, Widerruf, Reklamation, Bescheid, offer, order, contract, invoice, credit note, reminder, cancellation, termination;
  - it has an invoice-like attachment: a PDF or an XML / e-invoice (XRechnung, ZUGFeRD) attachment.

The keyword list lives in Archive Settings (new field, one keyword per line, empty = default list).

The suggestion is only a hint. It never archives anything. False positives (e.g. "Frage zur Rechnung") are acceptable because the rule says "when in doubt, archive". The criterion "part of an already archived thread" is dropped: `follow_threads` already archives later mails of an archived thread automatically, and the form already offers "Archive N more e-mails of this thread" for earlier ones.

## States

Every mail shown anywhere gets one of these states:

| State | Meaning | Look |
|---|---|---|
| archived | Business Document with `archive_started_at` set | green, "Archived" + link to the document (+ retain until, where there is room) |
| filed | Business Document exists, `archive_started_at` not set (not sealed yet) | blue, "Filed" + link, "sealed earliest on <date>" and a "Discard" action (`archiving.discard`); if it lacks a required counterparty, add "counterparty missing · removed on <date>" (cleanup deletes it after `keep_days`) |
| suggested | see above | yellow, prominent "Archive" button, expiry hint ("original expires in N days") |
| not archived | has an archivable original, not suggested (incl. automated / internal) | grey, small "Archive" link |
| ignored | original marked ignored | grey, "Ignored" + "undo" link (`inbox.unignore`), plus small "Archive" link |
| none | no original, no read permission, account not configured | nothing shown |

When `status.reason` is set (original pending / failed), the archive action stays disabled with the reason as tooltip, as today.

Users who can read the Communication but cannot create Business Documents see the indicators without any action. `status` / `statuses` return `can_archive: bool` for this (assistant's call).

`expires_on` is computed from `Mail Original.creation + keep_days`, like the cleanup job, and also set for filed documents that cleanup would remove. The existing `expires_at` in `inbox.candidates` stays as it is (assistant's call).

## Where it shows

### 1. Communication form (`communication.js`)

Replace the toolbar custom button with a headline bar above the mail content (`frm.dashboard.set_headline` or equivalent):

- **suggested / not archived, external, non-automated, not ignored:** yellow bar with the rule (short form), expiry hint, primary "Archive" button and a small "Don't archive" link (`inbox.ignore`). This bar is shown for every non-archived external non-automated mail in a Manual account, suggested or not; suggestion only adds a "Suggested: subject mentions 'Rechnung'"-style reason line. After "Don't archive" the bar turns into the grey ignored line with "undo".
- **automated or internal:** a single grey line "Automatic e-mail · archive" with a small link.
- **archived:** green bar "Archived as BD-… · retained until …", plus the existing "Archive N more e-mails of this thread" as a button inside the bar.

- **filed:** blue bar "Filed as BD-… · sealed earliest on <date>" with a "Discard" button (`docarchive.discard_documents`).

## Confirmation and undo

Clicking "Archive" only files the mail as a Business Document. It is sealed (WORM) by the nightly run (02:30) once it has been unchanged for `min_age_hours` (default 24), so the user has roughly 24–48 hours to discard it. Therefore (user's decision):

- The new one-click actions (form bar, timeline, list indicator) archive without an extra confirmation.
- The existing warning for automated / internal mails (`archive_with_warnings` in `mail_viewer.js`) stays everywhere.
- After a click, an alert "Filed as BD-… · Discard" appears, and the item switches to the blue filed state with its "Discard" action.
- "Sealed earliest on" = `modified + min_age_hours`, rounded up to the next nightly run; it is an estimate (missing required fields or "Archive now" change it).
- Existing flows (bulk action, mail viewer, inbox) keep their current behavior.

### 2. Activity timeline (any form)

On every form that has a timeline with Communication entries (Organization, Contact, Customer, …), docarchive decorates each Communication item:

- a small state indicator (dot + label) in the item header,
- an "Archive" action in the item's action row, next to Reply / Forward: a button for suggested, a link for not archived, nothing for archived (the label links to the document).

Frappe has no official extension point for this. docarchive enriches the rendered timeline after form refresh (DOM, one batch status call per refresh). Accepted risk: a Frappe update can break the decoration. It must fail silently — missing hints, never a broken form or a broken archive action.

### 3. Communication list (`communication_list.js`)

After the list renders, fetch states for the visible rows (same batch call) and append a small indicator to each row. Clicking the indicator of a suggested or not-archived row archives that mail (no extra confirmation, see below). No new field on Communication, no sortable or filterable column — the user only wants this if it doesn't dig deeper into Frappe. The existing bulk "Archive" in the Actions menu stays.

### 4. API for consumers (CRM "Heute")

- `docarchive.mail.archiving.status` gains `suggested: bool`, `suggestion_reasons: list[str]` (e.g. `["keyword:Rechnung", "attachment:pdf"]`) and `expires_on` (date the unarchived original is removed, from `keep_days`; null for archived or non-Manual).
- New whitelisted batch method `docarchive.mail.archiving.statuses(names)` returning `{communication: status}` with the same shape, permission-checked per row like `status`. Used by timeline, list and external consumers. Should replace the per-app helper `inocea_hub.mail_archive.statuses` in the CRM later.
- `docarchive.mail.inbox.candidates` rows gain the same `suggested` / `suggestion_reasons`.

The CRM changes (highlight suggested mails in "Heute", label the button) are done in inocea-crm after a docarchive release. Not part of this work.

## Goals

1. Archive state visible in form, timeline and list with one consistent look.
2. One-click archive from all three places, with an easy discard until sealing.
3. A short, configurable rule shown where the decision is made.
4. A configurable, explainable suggestion exposed through the status API.

## Non-goals

- Automatic archiving based on the suggestion.
- Machine learning or content analysis of the body or attachment contents.
- CRM-side changes (inocea-crm).
- A new field or column on Communication, list filters by archive state.
- Changes to the mail viewer page and the inbox beyond shared helpers.

## Anti-goals

- Sealing without a chance to discard: "Discard" must be visible wherever a filed mail shows. Sealing is irreversible (WORM, COMPLIANCE retention).
- Skipping the existing warning for automated / internal mails.
- A yellow bar on every notification mail — automated and internal mails stay quiet.
- A broken form, timeline or list because the decoration failed.
- One status request per timeline item or list row.

## Constraints

- Frappe v16, open-source app, release-please. CRM pins released versions, so the API change must be additive (existing keys of `status` keep their meaning).
- No changes to Frappe core files, no monkeypatching of module functions.
- Permission checks as in `status` / `access.readable`; the batch call must not leak state of unreadable mails.
- All UI strings translatable (`__()`, `_()`), English source.

## Decided by the user

- Form: headline bar variant (B) with rule, expiry and button.
- Grey line only for automated and internal mails.
- Timeline: archive action next to Reply, plus indicator with three states.
- Suggestion: content-based (keywords + attachments), configurable.
- When in doubt, archive.
- No extra confirmation for one-click archive; undo via "Discard" until sealing.
- List: lightweight indicators only, no deeper Frappe changes.

## Left to the builder (marked calls by the assistant)

- Exact DOM hooks for timeline items and list rows, and how to re-decorate after timeline re-render (assistant's call: hook form refresh and the list's refresh; observe the timeline container if needed).
- How attachments are detected (Communication's attached Files vs. parsed original) — pick the cheaper reliable one.
- Field names and placement in Archive Settings.
- Short form of the rule for the bar vs. full text in the tooltip.
- Tests: Python tests for suggestion and batch status; JS unit tests for pure state logic in a `*_core.js` module like the existing ones.
- Docs: update `docs/archiving.md` and `docs/host-apps.md` (API).
