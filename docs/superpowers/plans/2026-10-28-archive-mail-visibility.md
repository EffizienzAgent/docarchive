# Faster, More Visible Mail Archiving Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every mail's archive state (archived, filed, suggested, not archived, ignored) with a one-click archive action in the Communication form, every activity timeline and the Communication list, guided by a configurable rule and a configurable suggestion.

**Architecture:** A new server module `docarchive/mail/states.py` computes per-Communication states in one batch (`statuses(names)`); `archiving.status` delegates to it. `docarchive/mail/suggestion.py` holds the rule text, keywords and the pure suggestion check. On the client, pure rendering helpers live in `mail_core.js` (node-tested); a new `archive_states.js` batches status calls and offers `archive_one` / `discard_one` / `ignore_one`; form, timeline and list only wire DOM to these helpers.

**Tech Stack:** Frappe v16 (Python 3.14, `IntegrationTestCase`), vanilla JS/jQuery in the esbuild bundle, `node --test` for `*.test.mjs`.

**Spec:** `docs/superpowers/specs/2026-10-28-archive-mail-visibility-design.md`

## Global Constraints

- Additive API only: every existing key of `archiving.status` keeps its meaning (`original`, `archived`, `business_document`, `automated`, `internal`, `archive_status`, `retain_until`, `reason`).
- No Frappe core changes, no monkeypatching; timeline/list decoration must never throw into Frappe (wrap in try/catch, fail silently).
- One status request per form refresh / list refresh, never per row.
- Unreadable Communications return state `none` and leak nothing (same rule as today's `status`).
- Every new `_()` / `__()` string gets a German row in `docarchive/translations/de.csv` (`test_translations` enforces this).
- No extra confirmation for one-click archive; the existing automated/internal warning (`archive_with_warnings`) stays. Discard keeps its existing confirm.
- Existing flows (bulk list action, mail viewer, inbox) keep their behavior.
- Tests: `node --test 'docarchive/**/*.test.mjs'` and `bench --site docarchive.test run-tests --app docarchive --module <module>` (local dev bench: `$SCRATCH/dev/t.sh run-tests --app docarchive --module docarchive.tests.<name>`).

## Review Focus

1. A subject like "Auftragsbestätigung" or "Rechnungen 2026" must still count as keyword match (German compounds) — keywords match at word start, any suffix. Test in Task 1.
2. An original whose Business Document the user cannot read must not expose the document name, but state must still not be `suggested`/`not_archived`. Test in Task 2.
3. A Communication without Mail Original (other account, pre-feature mail) shows nothing anywhere. Test in Task 2 (`state == "none"`) and Task 3 (`archive_badge` returns `null`).
4. Subjects or document names with HTML must be escaped in bar, badges and alerts. Test in Task 3.
5. Timeline re-renders (new comment, new mail) must re-decorate without duplicate badges. Covered by an idempotent `decorate` that skips items that already carry `.docarchive-state`; checked manually in Task 5.

---

### Task 1: Rule and suggestion

**Files:**
- Create: `docarchive/mail/suggestion.py`
- Modify: `docarchive/document_archive/doctype/archive_settings/archive_settings.json` (after `follow_threads`: `archive_rule` Small Text, label "Archiving Rule", description "Shown next to the archive action. Empty: default rule."; `suggestion_keywords` Small Text, label "Suggestion Keywords", description "One per line. A mail whose subject contains one of them at the start of a word is suggested for archiving. Empty: default list.")
- Modify: `docarchive/translations/de.csv`
- Test: `docarchive/tests/test_mail_suggestion.py`

**Interfaces:**
- Produces:
  - `DEFAULT_KEYWORDS: tuple[str, ...]` = Angebot, Auftrag, Bestellung, Vertrag, Rechnung, Gutschrift, Mahnung, Kündigung, Widerruf, Reklamation, Bescheid, offer, order, contract, invoice, credit note, reminder, cancellation, termination
  - `DEFAULT_RULE: str` = the spec's rule text verbatim (English source)
  - `ATTACHMENT_KINDS = ("pdf", "xml")`
  - `keywords() -> list[str]` (settings or default; strip, drop empty)
  - `reasons(subject: str | None, file_names: list[str], words: list[str]) -> list[str]` → `["keyword:<word as configured>", ..., "attachment:pdf", "attachment:xml"]`, each kind at most once, keyword order as configured
  - `attachment_names(communications: list[str]) -> dict[str, list[str]]` from `File` rows with `attached_to_doctype="Communication"`
  - `@frappe.whitelist() rule() -> str` → `settings.archive_rule` or `_(DEFAULT_RULE)`

- [ ] **Step 1: Write the failing tests** in `test_mail_suggestion.py` (`IntegrationTestCase`):

```python
def test_keyword_matches_at_word_start_case_insensitive(self):
	self.assertEqual(suggestion.reasons("WG: Auftragsbestätigung 4711", [], ["Auftrag"]), ["keyword:Auftrag"])
	self.assertEqual(suggestion.reasons("Ihre rechnungen 2026", [], ["Rechnung"]), ["keyword:Rechnung"])
	self.assertEqual(suggestion.reasons("Unterauftrag", [], ["Auftrag"]), [])
	self.assertEqual(suggestion.reasons(None, [], ["Auftrag"]), [])

def test_multi_word_keyword(self):
	self.assertEqual(suggestion.reasons("Your credit note", [], ["credit note"]), ["keyword:credit note"])

def test_attachment_kinds_once_each(self):
	self.assertEqual(
		suggestion.reasons("Hi", ["a.PDF", "b.pdf", "x.xml", "c.png"], []), ["attachment:pdf", "attachment:xml"]
	)

def test_keywords_from_settings_or_default(self):
	frappe.db.set_single_value("Archive Settings", "suggestion_keywords", " Offerte \n\nAuftrag ")
	self.assertEqual(suggestion.keywords(), ["Offerte", "Auftrag"])
	frappe.db.set_single_value("Archive Settings", "suggestion_keywords", "")
	self.assertEqual(suggestion.keywords(), list(suggestion.DEFAULT_KEYWORDS))

def test_rule_from_settings_or_default(self):
	frappe.db.set_single_value("Archive Settings", "archive_rule", "")
	self.assertIn("When in doubt, archive.", suggestion.rule())
	frappe.db.set_single_value("Archive Settings", "archive_rule", "Alles mit Kunden.")
	self.assertEqual(suggestion.rule(), "Alles mit Kunden.")

def test_attachment_names_per_communication(self):
	# make a Communication with two attached File rows (pdf, png); one without
	# assert {comm_a: ["x.pdf", "y.png"], comm_b: []} (order by file_name)
```

Clear the settings cache (`frappe.clear_document_cache("Archive Settings")`) after each `set_single_value` and reset both fields in `tearDown`.

- [ ] **Step 2: Run** `t.sh run-tests --app docarchive --module docarchive.tests.test_mail_suggestion` → FAIL (module missing).
- [ ] **Step 3: Implement** `suggestion.py` and the two settings fields (`bench migrate` on the dev site). Keyword regex: `(?<!\w)` + `re.escape(word)`, `re.IGNORECASE`.
- [ ] **Step 4: Run** the module tests and `test_translations` → PASS.
- [ ] **Step 5: Commit** `feat: configurable archiving rule and suggestion keywords`.

---

### Task 2: Batch status API

**Files:**
- Create: `docarchive/mail/states.py`
- Modify: `docarchive/mail/archiving.py:206-243` (`status` delegates; new whitelisted `statuses`)
- Modify: `docarchive/mail/inbox.py` (`_item` gains `suggested`, `suggestion_reasons`)
- Test: `docarchive/tests/test_mail_states.py`

**Interfaces:**
- Consumes: `suggestion.keywords()`, `suggestion.reasons(...)`, `suggestion.attachment_names(...)`, `archiving.refusal(original)`, `access.readable(...)`, `accounts.account_row(...)`, `cleanup.DEFAULT_KEEP_DAYS`, `settings.get().min_age_hours`
- Produces:
  - `states.statuses(names: list[str]) -> dict[str, dict]`, one entry per requested name (dedupe, keep order)
  - `archiving.statuses(names)` — `@frappe.whitelist()`, accepts list or JSON string (use `_names`)
  - `archiving.status(communication) == archiving.statuses([communication])[communication]`
  - Status dict = existing keys + `mail_original: str | None`, `ignored: bool`, `state: "archived"|"filed"|"suggested"|"not_archived"|"ignored"|"none"`, `suggested: bool`, `suggestion_reasons: list[str]`, `expires_on: date | None`, `sealed_from: datetime | None`, `can_archive: bool`, `can_discard: bool`
  - `states.next_run_after(moment: datetime) -> datetime` — first 02:30 (site time) at or after `moment`

State rules (first match wins):
1. Communication unreadable or no Mail Original → `none` (unreadable keeps today's `reason`).
2. Original has `business_document`: `archived` if the document's `archive_status` in (`Archived`, `Deleted`), else `filed`. State uses the DB value even if the user cannot read the document; `business_document`/`archive_status`/`retain_until` stay hidden then (as `document_state` does today).
3. `ignored` → `ignored`.
4. Account row mode `Manual`, not `automated`, not `internal`, and `reasons(...)` non-empty → `suggested`.
5. Otherwise → `not_archived`.

Derived fields:
- `expires_on`: unfiled original → `(original.creation + keep_days).date()`; filed document that cleanup would remove (no `archive_started_at`, no counterparty, type `counterparty_required`, `hold_on_archive` 0) → `(document.creation + keep_days).date()`; else `None`. `keep_days` = account row value or `DEFAULT_KEEP_DAYS`.
- `sealed_from`: filed, `archive_started_at` not set, `Archive Settings.enabled` → `next_run_after(document.modified + min_age_hours)`; else `None`.
- `can_archive`: `frappe.has_permission("Business Document", "create")`.
- `can_discard`: filed, no `archive_started_at`, no `hold_on_archive`, user has write permission on the document.

Queries: one `get_all` on Mail Original, one on Business Document, one `attachment_names`, settings once — independent of the number of names.

- [ ] **Step 1: Write the failing tests** in `test_mail_states.py`, subclassing `MailArchivingTestCase`; use `eml(Subject=...)` (default subject "Offer" matches a keyword):

```python
def test_states_cover_each_case(self):
	plain = self.communication(); self.capture(eml(Subject="Kurze Frage"), plain)
	offer = self.communication(); self.capture(eml(Subject="Angebot 2027"), offer)
	filed = self.communication(); archiving.archive(self.capture(eml(Subject="Hallo"), filed))
	ignored = self.communication(); inbox.ignore([self.capture(eml(Subject="Rechnung"), ignored)])
	bare = self.communication()
	got = archiving.statuses([plain.name, offer.name, filed.name, ignored.name, bare.name])
	self.assertEqual(
		[got[c.name]["state"] for c in (plain, offer, filed, ignored, bare)],
		["not_archived", "suggested", "filed", "ignored", "none"],
	)
	self.assertEqual(got[offer.name]["suggestion_reasons"], ["keyword:Angebot"])

def test_automated_internal_and_automatic_mode_are_not_suggested(self): ...
	# automated mail with subject "Rechnung" → not_archived; internal → not_archived;
	# self.configure(mode="Automatic") + unarchived "Rechnung" → not_archived

def test_pdf_attachment_suggests(self): ...  # File attached to the Communication → ["attachment:pdf"]

def test_archived_state_and_status_compat(self):
	comm = self.communication(); name = archiving.archive(self.capture(comm=comm))
	frappe.db.set_value("Business Document", name, "archive_status", "Archived")
	self.assertEqual(archiving.status(comm.name), archiving.statuses([comm.name])[comm.name])
	self.assertEqual((archiving.status(comm.name)["state"], archiving.status(comm.name)["archived"]), ("archived", True))

def test_expires_on_from_keep_days(self):
	self.configure(keep_days=10)
	comm = self.communication(); original = self.capture(eml(Subject="Hi"), comm)
	creation = frappe.db.get_value("Mail Original", original, "creation")
	self.assertEqual(archiving.status(comm.name)["expires_on"], (creation + timedelta(days=10)).date())

def test_sealed_from_and_can_discard_for_filed(self):
	# settings enabled=1, min_age_hours=24; filed doc modified at 2026-10-28 03:00 (db_set modified)
	# → sealed_from == 2026-10-30 02:30, can_discard True; after setting archive_started_at → can_discard False, sealed_from None

def test_next_run_after(self):
	self.assertEqual(states.next_run_after(get_datetime("2026-10-29 02:00")), get_datetime("2026-10-29 02:30"))
	self.assertEqual(states.next_run_after(get_datetime("2026-10-29 02:30")), get_datetime("2026-10-29 02:30"))
	self.assertEqual(states.next_run_after(get_datetime("2026-10-29 03:00")), get_datetime("2026-10-30 02:30"))

def test_unreadable_communication_is_none_and_leaks_nothing(self):
	# outsider user without share → state "none", business_document None, suggestion_reasons []

def test_unreadable_document_is_filed_without_link(self):
	# like test_mail_document_status.test_unreadable_document_hides_them → state "filed", business_document None

def test_reader_without_create_cannot_archive(self):
	# shared-read outsider → can_archive False, state still computed

def test_query_count_is_constant(self):
	# 2 vs 6 communications: frappe.db.sql call count (patch/count) is equal
```

Add to `test_mail_candidates.py`: a candidate with subject "Rechnung" has `suggested=True`, `suggestion_reasons=["keyword:Rechnung"]`.

- [ ] **Step 2: Run** `test_mail_states`, `test_mail_candidates` → FAIL.
- [ ] **Step 3: Implement** `states.py`, wire `archiving.status`/`statuses`, extend `inbox._item` (compute reasons once per `candidates` call using one `attachment_names` call).
- [ ] **Step 4: Run** `test_mail_states`, `test_mail_candidates`, `test_mail_document_status`, `test_mail_archiving`, `test_mail_viewer` → PASS.
- [ ] **Step 5: Commit** `feat: batch archive status with suggestion, expiry and seal date`.

---

### Task 3: Client state helpers

**Files:**
- Modify: `docarchive/public/js/mail_core.js` (new pure exports)
- Create: `docarchive/public/js/archive_states.js`; import it in `docarchive.bundle.js` after `./mail_viewer`
- Modify: `docarchive/public/css/docarchive.bundle.css` (`.docarchive-state`, `.docarchive-state-bar`)
- Modify: `docarchive/translations/de.csv`
- Test: `docarchive/public/js/mail_core.test.mjs`

**Interfaces:**
- Produces (pure, in `mail_core.js`; `helpers = { t, link, date, rule }` as in `archive_headline`):
  - `archive_badge(status, helpers) -> null | { color, label_html, actions: string[] }` — colors: archived `green`, filed `blue`, suggested `yellow`, not_archived `gray`, ignored `gray`; `none` → `null`. `actions` subset of `"archive" | "discard" | "unignore"`, only when allowed (`can_archive` / `can_discard`) and `reason` empty for `archive`.
    - Labels (English source): "Archived", "Filed", "Should be archived", "Not archived", "Ignored".
  - `state_bar(status, helpers) -> null | { color, html }` — the form headline; buttons carry `data-docarchive-action="archive|discard|ignore|unignore"`:
    - suggested/not_archived (not automated, not internal): color `yellow`, html = short rule (`helpers.rule`), "Suggested: {0}" line for suggested (reasons rendered as "subject mentions “Rechnung”", "PDF attachment", "XML attachment"), "The original is removed on {0}." when `expires_on`, primary button "Archive", link "Don't archive".
    - not_archived automated/internal: color `gray`, "Automatic e-mail" / "Internal e-mail" + link "Archive".
    - ignored: `gray`, "Marked as not to be archived." + "Undo" + "Archive".
    - filed: `blue`, "Filed as {0}" + "Sealed on {0} at the earliest." when `sealed_from` + "Counterparty missing, removed on {0}." when `expires_on` + button "Discard" when `can_discard`.
    - archived: delegate to `archive_headline` (keeps today's texts).
    - `none`: `null`.
- Produces (browser, `archive_states.js`):
  - `docarchive.archive_states(names: string[]) -> Promise<Record<string, status>>` — collects calls within 30 ms into one `docarchive.mail.archiving.statuses` call
  - `docarchive.archive_rule() -> Promise<string>` — cached `docarchive.mail.suggestion.rule`
  - `docarchive.archive_one(communication, on_done)` — `docarchive.archive_communications([communication], ...)`; on success the single-item alert reads "Filed as {0}" plus a "Discard" link (`data-docarchive-discard`) that calls `docarchive.discard_documents`
  - `docarchive.ignore_one(mail_original, on_done)` / `docarchive.unignore_one(mail_original, on_done)` — `docarchive.mail.inbox.ignore` / `unignore`
  - `docarchive.bind_state_actions($root, status, on_done)` — delegates clicks on `[data-docarchive-action]` to the functions above

- [ ] **Step 1: Write the failing node tests** in `mail_core.test.mjs`:

```js
test("badge per state", async () => {
	const { archive_badge } = await import("./mail_core.js");
	const h = { t, link: (n) => `<a>${n}</a>`, date: (v) => v, rule: "R" };
	assert.equal(archive_badge({ state: "none" }, h), null);
	assert.deepEqual(archive_badge({ state: "suggested", can_archive: true }, h).actions, ["archive"]);
	assert.equal(archive_badge({ state: "suggested", can_archive: true, reason: "pending" }, h).actions.length, 0);
	assert.deepEqual(archive_badge({ state: "filed", business_document: "D-1", can_discard: true }, h).actions, ["discard"]);
	assert.equal(archive_badge({ state: "suggested", can_archive: false }, h).actions.length, 0);
	assert.equal(archive_badge({ state: "archived", business_document: "D-1" }, h).color, "green");
	assert.deepEqual(archive_badge({ state: "ignored", can_archive: true }, h).actions, ["unignore", "archive"]);
});

test("bar escapes reasons and names", async () => {
	const { state_bar } = await import("./mail_core.js");
	const bar = state_bar({ state: "suggested", can_archive: true, suggestion_reasons: ["keyword:<b>x</b>"] }, h);
	assert.match(bar.html, /&lt;b&gt;x&lt;\/b&gt;/);
	assert.match(bar.html, /data-docarchive-action="archive"/);
	assert.match(bar.html, /data-docarchive-action="ignore"/);
});

test("automated mail gets a quiet gray line", ...);   // color gray, no "R" rule text
test("filed bar shows seal date and discard", ...);  // "Sealed on 2026-10-30 02:30 at the earliest", data-docarchive-action="discard"
test("filed bar without discard right has no button", ...);
test("archived delegates to archive_headline", ...);
```

- [ ] **Step 2: Run** `node --test 'docarchive/**/*.test.mjs'` → FAIL.
- [ ] **Step 3: Implement** the pure helpers, then `archive_states.js`; add German rows.
- [ ] **Step 4: Run** node tests → PASS; `t.sh run-tests --app docarchive --module docarchive.tests.test_translations` → PASS; `bench build --app docarchive` succeeds.
- [ ] **Step 5: Commit** `feat: client helpers for archive state badges and bar`.

---

### Task 4: Communication form bar

**Files:**
- Modify: `docarchive/public/js/communication.js`

**Interfaces:**
- Consumes: `docarchive.archive_states`, `docarchive.archive_rule`, `mail_core.state_bar`, `docarchive.bind_state_actions`, `docarchive.set_archive_headline`

- [ ] **Step 1:** Replace the toolbar button: on refresh, `Promise.all([archive_states([name]), archive_rule()])` → `state_bar(...)` → `frm.dashboard.set_headline(html, color)`; bind actions on `frm.dashboard.$headline` with `on_done = () => frm.reload_doc()`. Keep `docarchive_thread_pending` for the archived/filed states but render its button inside the bar (`data-docarchive-action="thread"`), not in the toolbar.
- [ ] **Step 2: Verify in the dev bench** (create a mail account in Manual mode, capture a mail via `bench execute` or the demo data `bench --site docarchive.test execute docarchive.demo...`/`docarchive commands`): suggested mail shows yellow bar with rule and "Archive"; click → blue "Filed" bar with seal date and "Discard"; automated mail shows the gray line; ignored → "Undo" works. Use Chrome tools for a screenshot.
- [ ] **Step 3: Commit** `feat: archive bar on the Communication form`.

---

### Task 5: Timeline decoration

**Files:**
- Create: `docarchive/public/js/timeline_states.js`; import in `docarchive.bundle.js`

**Interfaces:**
- Consumes: `docarchive.archive_states`, `mail_core.archive_badge`, `docarchive.bind_state_actions`

- [ ] **Step 1:** Look up the v16 timeline DOM in `apps/frappe/frappe/public/js/frappe/form/footer/form_timeline.js` (Communication items, their name attribute, the action row with Reply / Forward) and the form event that fires after the timeline renders. Record the selectors as constants at the top of the file.
- [ ] **Step 2: Implement** `decorate(frm)`: collect Communication names of timeline items without `.docarchive-state`, one `archive_states` call, insert the badge in the item header and the action (button for suggested, link otherwise) next to Reply. Re-run on form refresh and when the timeline container changes (`MutationObserver`, debounced 100 ms, disconnected on form unload). Whole function in `try/catch` → `console.debug`.
- [ ] **Step 3: Verify in the dev bench:** a Contact with three linked mails shows three states; archiving from the timeline updates only that item; adding a comment does not duplicate badges; forms without timeline are unaffected.
- [ ] **Step 4: Commit** `feat: archive state and action in activity timelines`.

---

### Task 6: Communication list indicators

**Files:**
- Modify: `docarchive/public/js/communication_list.js`

**Interfaces:**
- Consumes: `docarchive.archive_states`, `mail_core.archive_badge`, `docarchive.archive_one`

- [ ] **Step 1: Implement** a chained `settings.refresh(listview)` (keep any existing `refresh`, like `onload` does): names of `listview.data` → one `archive_states` call → append badge to each row (row found via `.list-row-checkbox[data-name]`, CSS-escaped). Clicking a suggested / not-archived badge calls `archive_one(name, () => listview.refresh())`; the click must not open the row (`stopPropagation`). try/catch as in Task 5.
- [ ] **Step 2: Verify in the dev bench:** badges appear, paging and filters re-decorate, bulk "Archive" still works.
- [ ] **Step 3: Commit** `feat: archive state indicators in the Communication list`.

---

### Task 7: Docs and changelog-ready commit

**Files:**
- Modify: `docs/archiving.md` (states, rule, suggestion, settings fields, 24–48 h discard window), `docs/host-apps.md` (`statuses` API and new `status` keys, `inbox.candidates` new keys)

- [ ] **Step 1:** Write the doc sections (English, matching existing tone).
- [ ] **Step 2:** Full run: node tests + `t.sh run-tests --app docarchive` → all PASS.
- [ ] **Step 3: Commit** `docs: archive states, rule and suggestion`.
