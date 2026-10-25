# Business Correspondence Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the original MIME bytes of mails that Frappe receives and sends, archive selected ones as Business Documents of type "Correspondence", and show them readably with their thread.

**Architecture:** A new package `docarchive/mail/` holds everything. Original bytes are captured at receive/send time through `extend_doctype_class` mixins on `Email Account` and `Email Queue` and stored as a private File attached to a new doctype **Mail Original** (one per Communication). Archiving creates a Business Document that attaches a copy of that File (Frappe dedupes it to the same `file_url`). The existing nightly run, locks and storage targets do the rest.

**Tech Stack:** Frappe v16 (16.53, pinned in `.github/versions.env`), Python 3.14 stdlib `email` package, Frappe desk JS (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-10-25-correspondence-design.md`

## Global Constraints

- No new Python or JS dependencies.
- Mail receipt and sending must never fail because of docarchive: every entry point called from Frappe's receive/send path wraps its work in a DB savepoint, rolls back to it on any exception, logs with `frappe.log_error(title="docarchive mail capture")`, and returns normally.
- No changes to Frappe files; no monkeypatching of module-level functions. Wrapping a method on a single `InboundMail` instance is allowed.
- Every user-visible string in Python (`_()`), JS (`__()`), doctype labels, descriptions and Select options needs a German entry in `docarchive/translations/de.csv` (enforced by `tests/test_translations.py`).
- Code style: tabs, ruff config in `pyproject.toml`, few comments, small functions, like the existing modules.
- Defaults from the spec: mode `Off`, archive internal mails off, contacts count as counterparty off, keep unarchived originals 90 days.
- Seeded types: "Correspondence" (slug `correspondence`, 6 years, counterparty required) and "Internal Correspondence" (slug `internal-correspondence`, 6 years, counterparty not required). Both `archive: 1`, retention from document date.
- Automated mail: header `List-Unsubscribe` present, or `Auto-Submitted` present and not `no` (case-insensitive), or `Precedence` in `bulk`, `list`, `junk`.
- Tests run in a bench like CI (`.github/workflows/test.yml`): `bench --site docarchive.test run-tests --app docarchive --module <module>`. Set up your own environment (e.g. a compose file modelled on CI); do not reuse or touch the running `da11-*` containers, which belong to another session.

## Review Focus

1. A mail without `Message-ID` → still captured; dedupe falls back to SHA-256; `thread_key` is `sha256:<hex>` so the thread view shows it alone. Test in Task 3 and Task 5.
2. Encoded-word subjects and addresses (`=?utf-8?q?...?=`, Latin-1 bodies, broken charsets) → subject and title are decoded text, parsing never raises. Test in Task 3 and Task 7.
3. `raw_message` arriving as `str` instead of `bytes` (POP3 and some services) → stored as UTF-8 with `surrogateescape`, SHA-256 over the stored bytes. Test in Task 3.
4. The same Message-ID captured twice (two connected inboxes, or sent and then received internally) → one Business Document per own company; archiving the second returns the first. Test in Task 5.
5. The Communication is deleted after its mail was archived → Business Document, file and viewer keep working; an unarchived Mail Original is deleted with its Communication. Test in Task 4 and Task 7.

---

### Task 1: Settings, seeded types and the Mail Original doctype

**Files:**
- Create: `docarchive/document_archive/doctype/archive_mail_account/` (child table: `.json`, `.py`, `__init__.py`)
- Create: `docarchive/document_archive/doctype/mail_original/` (`.json`, `.py`, `__init__.py`)
- Create: `docarchive/mail/__init__.py`, `docarchive/mail/accounts.py`
- Modify: `docarchive/document_archive/doctype/archive_settings/archive_settings.json` (new section "Correspondence" with table `mail_accounts`)
- Modify: `docarchive/document_types.py`, `docarchive/setup.py`
- Modify: `docarchive/translations/de.csv`
- Test: `docarchive/tests/test_mail_accounts.py`, `docarchive/tests/test_install.py`, `docarchive/tests/test_translations.py`

**Interfaces:**
- Produces:
  - Child doctype **Archive Mail Account** (`istable`), fields: `email_account` (Link Email Account, reqd, in list view), `mode` (Select `Off\nManual\nAutomatic`, default `Off`), `archive_internal` (Check, 0), `contacts_as_counterparty` (Check, 0), `keep_days` (Int, default 90), `own_company` (Link Own Company), `internal_domains` (Small Text, description: comma-separated; empty = domain of the account's address). Archive Settings validates that an e-mail account appears at most once.
  - Doctype **Mail Original** (module Document Archive, not submittable, `track_changes` off, permissions: System Manager read/delete only), fields: `communication` (Link Communication, reqd, unique), `email_account` (Link Email Account), `origin` (Data), `file` (Attach), `sha256` (Data), `size` (Int), `direction` (Select `Incoming\nOutgoing`), `message_id` (Data, search index), `in_reply_to` (Data), `mail_references` (Long Text), `thread_key` (Data, search index), `sender` (Data), `to` (Small Text), `cc` (Small Text), `subject` (Small Text), `date_header` (Data), `mail_at` (Datetime), `automated` (Check), `internal` (Check), `business_document` (Link Business Document). All read-only.
  - `docarchive.mail.accounts.account_settings(email_account: str | None) -> frappe._dict | None` — the row, or `None` if missing or mode `Off`.
  - `docarchive.mail.accounts.internal_domains(row) -> set[str]` — lower-case domains from the row, else the domain of the Email Account's `email_id`.
  - `docarchive.document_types.MAIL_TYPES` (the two seeded types, values from Global Constraints), `CORRESPONDENCE = "Correspondence"`, `INTERNAL_CORRESPONDENCE = "Internal Correspondence"`, and `ensure_mail_types()` which inserts each missing type (by `type_name`) and is called from `setup.apply_defaults()` after `seed_types()`.

- [ ] **Step 1: Write the failing tests**

```python
# test_mail_accounts.py
def test_missing_account_has_no_settings(self):
	self.assertIsNone(accounts.account_settings("nope@example.test"))

def test_off_account_has_no_settings(self):
	make_mail_settings(account, mode="Off")
	self.assertIsNone(accounts.account_settings(account))

def test_row_defaults(self):
	row = make_mail_settings(account, mode="Manual")
	self.assertEqual((row.keep_days, row.archive_internal, row.contacts_as_counterparty), (90, 0, 0))

def test_internal_domains_default_to_account_domain(self):
	# account email_id "info@acme.test"
	self.assertEqual(accounts.internal_domains(accounts.account_settings(account)), {"acme.test"})

def test_internal_domains_from_row(self):
	row = make_mail_settings(account, mode="Manual", internal_domains="Acme.test, acme.de")
	self.assertEqual(accounts.internal_domains(row), {"acme.test", "acme.de"})

def test_duplicate_account_rows_refused(self): ...  # assertRaises(frappe.ValidationError)

# test_install.py
def test_mail_types_added_to_existing_install(self):
	frappe.delete_doc("Business Document Type", "Correspondence", force=True)
	setup.apply_defaults()
	t = frappe.get_doc("Business Document Type", "Correspondence")
	self.assertEqual((t.slug, t.default_retention_years, t.counterparty_required), ("correspondence", 6, 1))
	self.assertEqual(frappe.db.get_value("Business Document Type", "Internal Correspondence", "counterparty_required"), 0)
```

Add `make_email_account(email_id)` (incoming/outgoing disabled) and `make_mail_settings(email_account, **fields)` to `tests/factories.py`; `make_mail_settings` replaces the `mail_accounts` table with one row and returns it. Extend `test_seeded_types_translated` with the two new type names.

- [ ] **Step 2: Run them and see them fail**

Run: `bench --site docarchive.test run-tests --app docarchive --module docarchive.tests.test_mail_accounts`
Expected: FAIL (ImportError / doctype missing)

- [ ] **Step 3: Add the doctypes, settings section, `accounts.py`, `ensure_mail_types()`, translations**

- [ ] **Step 4: Run `test_mail_accounts`, `test_install`, `test_translations`, `test_settings`**

Expected: PASS

- [ ] **Step 5: Commit** — `feat: mail account settings, mail original doctype and correspondence types`

---

### Task 2: Mail fields on Business Document

**Files:**
- Modify: `docarchive/document_archive/doctype/business_document/business_document.json`, `business_document.py`
- Modify: `docarchive/translations/de.csv`
- Test: `docarchive/document_archive/doctype/business_document/test_business_document.py`

**Interfaces:**
- Produces: fields in a collapsible section "Mail" with `depends_on: eval:doc.mail_message_id || doc.mail_thread_key`: `mail_direction` (Select `\nIncoming\nOutgoing`), `mail_message_id` (Data, search index), `mail_in_reply_to` (Data), `mail_references` (Long Text), `mail_thread_key` (Data, search index), `mail_at` (Datetime). All read-only.
- Produces: `MAIL_FIELDS` tuple in `business_document.py`, added to both `LOCKED_FIELDS` and `PROTECTED_FIELDS`.
- Produces: `validate_file` accepts the extension `eml` when `source_doctype == "Communication"`, regardless of Allowed File Types.

- [ ] **Step 1: Write the failing tests**

```python
def test_mail_fields_are_server_only(self):
	doc = make_business_document(mail_message_id="a@b")   # archive_write False
	self.assertFalse(doc.mail_message_id)

def test_mail_fields_locked_after_archive_start(self):
	doc = make_business_document(archive_write=True, mail_message_id="a@b")
	start_archiving(doc)          # existing helper/pattern in this test file
	doc.mail_message_id = "c@d"; doc.flags.archive_write = False
	doc.save()
	self.assertEqual(doc.reload().mail_message_id, "a@b")

def test_eml_allowed_for_communication_source(self):
	file = make_file(content=EML, file_name="m.eml")
	doc = make_business_document(archive_write=True, file=file.file_url, source_doctype="Communication", source_name=comm.name)
	self.assertTrue(doc.content_sha256)

def test_eml_refused_for_other_sources(self):
	self.assertRaises(frappe.ValidationError, make_business_document, file=make_file(content=EML, file_name="m.eml").file_url)
```

Add `EML` (a small RFC 5322 message as bytes) and `eml(**headers)` builder to `tests/factories.py`; later tasks reuse it.

- [ ] **Step 2: Run and see them fail**

Run: `bench --site docarchive.test run-tests --app docarchive --module docarchive.document_archive.doctype.business_document.test_business_document`

- [ ] **Step 3: Add fields, `MAIL_FIELDS`, the `eml` rule, translations**

- [ ] **Step 4: Run the business document tests, `test_guards`, `test_translations`** — Expected: PASS

- [ ] **Step 5: Commit** — `feat: mail fields on business documents`

---

### Task 3: Capturing originals

**Files:**
- Create: `docarchive/mail/originals.py`, `docarchive/mail/headers.py`
- Modify: `docarchive/mail/__init__.py` (`from docarchive.mail.originals import capture`)
- Test: `docarchive/tests/test_mail_capture.py`

**Interfaces:**
- Consumes: `accounts.account_settings`, `accounts.internal_domains` (Task 1).
- Produces:
  - `docarchive.mail.headers.parse(raw: bytes) -> frappe._dict` with `message_id`, `in_reply_to`, `references` (list, angle brackets stripped like Frappe's `get_string_between`), `sender`, `to`, `cc` (decoded strings), `subject` (decoded), `date_header`, `automated` (bool, rule from Global Constraints). Uses `email.message_from_bytes(raw, policy=email.policy.default)`; never raises on bad headers (falls back to empty values).
  - `docarchive.mail.headers.thread_key(parsed, sha256: str) -> str` — first of `references`, else `in_reply_to`, else `message_id`, else `f"sha256:{sha256}"`.
  - `docarchive.mail.headers.is_internal(parsed, domains: set[str]) -> bool` — True when every address in sender, to and cc has a domain in `domains`.
  - `docarchive.mail.capture(communication, raw: bytes | str, origin: str) -> str | None` — returns the Mail Original name, or `None` when the Communication has no configured account (`email_account` missing or mode Off) or capture failed. Idempotent: a second call for the same Communication returns the existing name. `str` input is encoded with `"utf-8", "surrogateescape"`. Stores the File (`file_name` = `slug(message_id or communication.name)` + `.eml`, private, attached to the Mail Original), all header fields, `direction` from `sent_or_received` (`Sent` → Outgoing), `mail_at` = `communication_date`. Runs in a savepoint and never raises. After capture it calls `docarchive.mail.archiving.auto_archive(name)` (Task 5; until then a stub that does nothing).

- [ ] **Step 1: Write the failing tests**

```python
def test_capture_stores_exact_bytes(self):
	name = mail.capture(comm, EML, "frappe-receive")
	original = frappe.get_doc("Mail Original", name)
	self.assertEqual(content.read_bytes(original.file), EML)
	self.assertEqual(original.sha256, hashlib.sha256(EML).hexdigest())

def test_headers_and_direction(self): ...  # message_id, in_reply_to, sender, subject, direction Incoming/Outgoing

def test_str_raw_is_stored_as_utf8(self):
	name = mail.capture(comm, EML.decode(), "test")
	self.assertEqual(content.read_bytes(frappe.db.get_value("Mail Original", name, "file")), EML)

def test_encoded_subject_is_decoded(self):
	raw = eml(Subject="=?utf-8?q?Angebot_f=C3=BCr_M=C3=BCller?=")
	self.assertEqual(frappe.db.get_value("Mail Original", mail.capture(comm, raw, "t"), "subject"), "Angebot für Müller")

def test_without_message_id_thread_key_is_hash(self): ...  # startswith "sha256:"

def test_automated_markers(self):
	for header in ({"List-Unsubscribe": "<mailto:x@y>"}, {"Auto-Submitted": "auto-replied"}, {"Precedence": "bulk"}):
		self.assertTrue(headers.parse(eml(**header)).automated)
	self.assertFalse(headers.parse(eml(**{"Auto-Submitted": "no"})).automated)

def test_internal_detection(self): ...  # all addresses @acme.test → True; one external → False

def test_off_account_is_not_captured(self):
	self.assertIsNone(mail.capture(comm_off, EML, "t"))

def test_second_capture_returns_existing(self): ...

def test_failure_does_not_raise(self):
	with patch("docarchive.mail.originals.store_file", side_effect=Exception("boom")):
		self.assertIsNone(mail.capture(comm, EML, "t"))
	self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))
```

Add `make_communication(email_account, sent_or_received="Received", **fields)` to factories.

- [ ] **Step 2: Run and see them fail** — `--module docarchive.tests.test_mail_capture`
- [ ] **Step 3: Implement `headers.py` and `originals.py`**
- [ ] **Step 4: Run `test_mail_capture`** — Expected: PASS
- [ ] **Step 5: Commit** — `feat: capture original mail bytes`

---

### Task 4: Hooks into receiving, sending and deleting

**Files:**
- Create: `docarchive/mail/extensions.py`
- Modify: `docarchive/hooks.py`, `docarchive/mail/originals.py`
- Test: `docarchive/tests/test_mail_hooks.py`

**Interfaces:**
- Consumes: `docarchive.mail.capture` (Task 3).
- Produces:
  - `extend_doctype_class = {"Email Account": ["docarchive.mail.extensions.EmailAccountArchive"], "Email Queue": ["docarchive.mail.extensions.EmailQueueArchive"]}` in `hooks.py`.
  - `EmailAccountArchive.get_inbound_mails(self)`: calls `super()`, then replaces each mail's `process` with a wrapper that calls the original, and if it returned a Communication calls `capture(communication, mail.raw_message, "frappe-receive")`. Origin for `service == "Frappe Mail"` is `"frappe-mail"`.
  - `EmailQueueArchive.send(self, *args, **kwargs)`: calls `super().send(...)`, then `originals.capture_sent(self)`.
  - `originals.capture_sent(queue) -> str | None`: skips queues without `communication` or with `reference_doctype == "Newsletter"`; takes the first recipient whose `is_mail_sent()` is true after reload; rebuilds that recipient's bytes with `SendMailContext(queue)` (set `ctx.email_account_doc = queue.get_email_account(raise_error=True)` first, then `ctx.build_message(recipient)`), and captures with origin `"frappe-send"`. Never raises.
  - `doc_events["Communication"]["on_trash"] = "docarchive.mail.originals.on_communication_trash"`: deletes the Mail Original and its File when `business_document` is empty; leaves it otherwise.

- [ ] **Step 1: Write the failing tests**

```python
def test_receive_captures_raw(self):
	mail = InboundMail(EML, account_doc, uid="1")
	account_doc_ext = frappe.get_doc("Email Account", account)       # extended class
	with patch.object(type(account_doc_ext).__mro__[1], "get_inbound_mails", return_value=[mail]):  # or patch the base EmailAccount.get_inbound_mails
		account_doc_ext.receive()
	comm = frappe.get_last_doc("Communication", filters={"message_id": MESSAGE_ID})
	self.assertTrue(frappe.db.exists("Mail Original", {"communication": comm.name}))

def test_receive_succeeds_when_capture_fails(self):
	with patch("docarchive.mail.originals.store_file", side_effect=Exception("boom")):
		...receive()
	self.assertTrue(frappe.db.exists("Communication", {"message_id": MESSAGE_ID}))

def test_send_captures_sent_bytes(self):
	# frappe.sendmail(..., communication=comm.name, now=True) with one recipient
	original = frappe.get_doc("Mail Original", {"communication": comm.name})
	self.assertEqual(content.read_bytes(original.file), frappe.safe_encode(frappe.flags.sent_mail))
	self.assertEqual(original.direction, "Outgoing")

def test_newsletter_not_captured(self): ...

def test_deleting_communication_deletes_unarchived_original(self): ...

def test_deleting_communication_keeps_archived_original(self): ...  # set business_document via db_set
```

- [ ] **Step 2: Run and see them fail** — `--module docarchive.tests.test_mail_hooks`
- [ ] **Step 3: Implement `extensions.py`, `capture_sent`, `on_communication_trash`, hooks**
- [ ] **Step 4: Run `test_mail_hooks` and `test_mail_capture`** — Expected: PASS
- [ ] **Step 5: Commit** — `feat: capture mails on receive and send`

---

### Task 5: Archiving, counterparty, automatic mode and discard

**Files:**
- Create: `docarchive/mail/archiving.py`
- Modify: `docarchive/hooks.py` (`docarchive_assign = ["docarchive.mail.archiving.assign"]`), `docarchive/mail/originals.py` (call the real `auto_archive`)
- Modify: `docarchive/translations/de.csv`
- Test: `docarchive/tests/test_mail_archiving.py`

**Interfaces:**
- Consumes: Mail Original (Task 1), Business Document mail fields (Task 2), `capture` (Task 3).
- Produces:
  - `archive(original_name: str) -> str` — returns the Business Document name. Type: `INTERNAL_CORRESPONDENCE` if `original.internal`, else `CORRESPONDENCE`. Own company: `settings.own_company_for(mail_at, row.own_company)`. Dedupe: an existing Business Document with the same own company and `mail_message_id` (or, without Message-ID, the same `content_sha256` and `source_doctype = "Communication"`) is returned and linked in `original.business_document`. Otherwise inserts with `flags.archive_write = True`: `document_date = getdate(mail_at)`, `title = subject or _("(no subject)")` (max 140 chars), `source_system = "email"`, `source_account = email_account`, `source_doctype = "Communication"`, `source_name`, `source_party_email` = sender for Incoming, first `to` address for Outgoing, the `mail_*` fields from the original; then attaches a new private File with the same bytes to the document, sets `file`, saves, and sets `original.business_document`.
  - `refusal(original_name: str | None) -> str | None` — `_("No original was captured for this e-mail.")` if none; else `None`.
  - `auto_archive(original_name: str) -> str | None` — only for mode `Automatic`, not `automated`, and (`archive_internal` or not `internal`); calls `archive`. Runs inside the caller's savepoint.
  - `assign(doc)` — `docarchive_assign` hook. Does nothing unless `doc.source_doctype == "Communication"` and no counterparty. Candidates from the Communication's `timeline_links`: drop Contacts whose every e-mail address is internal (`accounts.internal_domains` of the account in `doc.source_account`); a Contact with dynamic links is replaced by those links; a Contact without links counts only if the row's `contacts_as_counterparty` is on; keep only doctypes allowed by `counterparty.doctypes()` (empty = any); dedupe. Exactly one left → set `counterparty_type`, `counterparty`.
  - `@frappe.whitelist() status(communication: str) -> dict` — `{"original": bool, "business_document": str | None, "automated": bool, "internal": bool, "reason": str | None}`; requires read on the Communication.
  - `@frappe.whitelist(methods=["POST"]) archive_communications(names) -> dict` — `{"archived": {communication: business_document}, "refused": {communication: reason}}`; requires read on each Communication and create on Business Document.
  - `@frappe.whitelist(methods=["POST"]) discard(names) -> dict` — `{"discarded": [names], "refused": {name: reason}}`. Only Business Documents with `source_doctype == "Communication"` and no `archive_started_at`; requires write on the document. Deletes the document (with `ignore_permissions`) and its Mail Original and File.

- [ ] **Step 1: Write the failing tests**

```python
def test_archive_creates_document(self):
	name = archiving.archive(original)
	doc = frappe.get_doc("Business Document", name)
	self.assertEqual((doc.document_type, doc.mail_direction, doc.source_doctype), ("Correspondence", "Incoming", "Communication"))
	self.assertEqual(content.read_bytes(doc.file), EML)
	self.assertEqual(frappe.db.get_value("Mail Original", original, "business_document"), name)

def test_same_message_id_twice_gives_one_document(self): ...
def test_without_message_id_dedupes_by_hash(self): ...
def test_internal_mail_gets_internal_type(self): ...

def test_counterparty_from_contact_link(self):   # Contact linked to Customer → Customer
def test_contact_without_link_only_when_enabled(self):
def test_two_parties_stay_unassigned(self):
def test_internal_contacts_ignored(self):
def test_link_added_later_is_taken_by_reassign(self):   # run.reassign()

def test_automatic_mode_archives_on_capture(self):
def test_automatic_mode_skips_automated_and_internal(self):
def test_manual_mode_does_not_archive_on_capture(self):

def test_archive_communications_refuses_without_original(self):
	result = archiving.archive_communications([comm_without_original.name])
	self.assertEqual(result["refused"][comm_without_original.name], "No original was captured for this e-mail.")

def test_archive_communications_needs_create_permission(self):  # outsider → PermissionError

def test_discard_removes_document_and_original(self):
def test_discard_refuses_archived(self):
```

- [ ] **Step 2: Run and see them fail** — `--module docarchive.tests.test_mail_archiving`
- [ ] **Step 3: Implement `archiving.py`, register the hook, translations**
- [ ] **Step 4: Run `test_mail_archiving`, `test_assignment`, `test_run`, `test_mail_capture`** — Expected: PASS
- [ ] **Step 5: Commit** — `feat: archive captured mails as correspondence`

---

### Task 6: Cleanup and the mail section of the meta

**Files:**
- Create: `docarchive/mail/cleanup.py`
- Modify: `docarchive/hooks.py` (`scheduler_events["daily"] = ["docarchive.mail.cleanup.run"]`), `docarchive/run.py` (`meta()`)
- Test: `docarchive/tests/test_mail_cleanup.py`, `docarchive/tests/test_run.py`

**Interfaces:**
- Consumes: Mail Original, `archiving.discard` logic (reuse the deleting helper, not the whitelisted wrapper).
- Produces:
  - `cleanup.run() -> dict` — `{"originals": n, "documents": m}`. Per configured account, cutoff = now − `keep_days`: deletes Mail Originals with `creation < cutoff` and no `business_document`; deletes Business Documents with `source_doctype = "Communication"`, `source_account` = the account, `archive_started_at` not set, `counterparty` not set, `creation < cutoff`, together with their Mail Original. Accounts with mode Off are skipped too (their originals stay until the account is configured again; my call, keeps a switch-off from wiping data).
  - `run.meta(doc, years, until)` gets `"mail": mail_meta(doc)`, `None` for non-mail documents. `mail_meta` reads the Mail Original with `business_document = doc.name` and returns `direction, message_id, in_reply_to, references (list), thread_key, from, to, cc, subject, date_header, received_or_sent_at (UTC ISO via utc_iso), account, origin` and, for origin `frappe-send`, `"note": "Copies to other recipients differ only in tracking and unsubscribe links."`. Format string stays `docarchive-meta/1`.

- [ ] **Step 1: Write the failing tests**

```python
def test_old_unarchived_original_deleted(self):   # creation set back 91 days with frappe.db.set_value(..., "creation", ...)
def test_recent_original_kept(self):
def test_archived_original_kept(self):
def test_old_document_without_counterparty_deleted(self):
def test_old_document_with_counterparty_kept(self):
def test_started_document_kept(self):

# test_run.py
def test_meta_has_mail_section(self):
	meta = run.meta(doc, 6, until)
	self.assertEqual(meta["mail"]["message_id"], MESSAGE_ID)
	self.assertEqual(meta["mail"]["direction"], "Incoming")
def test_meta_without_mail_is_none(self):
	self.assertIsNone(run.meta(plain_doc, 8, until)["mail"])
```

- [ ] **Step 2: Run and see them fail** — `--module docarchive.tests.test_mail_cleanup`
- [ ] **Step 3: Implement `cleanup.py`, `mail_meta`, scheduler entry**
- [ ] **Step 4: Run `test_mail_cleanup`, `test_run`, `test_minio` (if MinIO is up)** — Expected: PASS
- [ ] **Step 5: Commit** — `feat: clean up unarchived mails and add mail meta`

---

### Task 7: Viewer and thread API

**Files:**
- Create: `docarchive/mail/viewer.py`, `docarchive/mail/thread.py`
- Test: `docarchive/tests/test_mail_viewer.py`

**Interfaces:**
- Consumes: Business Document `file`, `content_sha256`, `mail_*` fields; `content.read_bytes`.
- Produces:
  - `@frappe.whitelist() viewer.render(name: str) -> dict` — requires read on the Business Document and a `.eml` file. Returns `{"headers": {"from", "to", "cc", "date", "subject", "message_id"}, "html": str | None, "text": str | None, "remote_images": bool, "attachments": [{"index": int, "file_name": str, "content_type": str, "size": int}], "sha256_ok": bool}`. HTML: preferred `text/html` part, passed through `frappe.utils.html_utils.sanitize_html`; `cid:` references replaced by `data:` URIs of the related parts; `src` of remote images (`http:`/`https:`) moved to `data-remote-src`. `attachments` come from `msg.iter_attachments()` in order; `index` is the position.
  - `@frappe.whitelist() viewer.attachment(name: str, index: int)` — same permission; sets `frappe.response.filename`, `filecontent`, `type = "download"`.
  - `@frappe.whitelist() thread.thread(name: str) -> list[dict]` — Business Documents (via `frappe.get_list`) with the same `mail_thread_key` and own company, ordered by `mail_at`. Items: `{"kind": "mail", "name", "title", "direction", "mail_at", "sender"}`; for each `mail_in_reply_to` not among the listed Message-IDs one `{"kind": "gap", "message_id", "communication": str | None}` placed before the replying mail, where `communication` is set if a Mail Original with that Message-ID exists without `business_document` and the user can read its Communication.

- [ ] **Step 1: Write the failing tests**

```python
def test_render_headers_and_text(self): ...
def test_script_removed_and_remote_images_blocked(self):
	r = viewer.render(doc_with_html('<p>Hi</p><script>x()</script><img src="https://t.example/p.gif">'))
	self.assertNotIn("<script", r["html"]); self.assertIn('data-remote-src="https://t.example/p.gif"', r["html"]); self.assertTrue(r["remote_images"])
def test_cid_image_inlined(self): ...           # 'src="data:image/png;base64,'
def test_attachments_listed_and_downloadable(self): ...
def test_sha_mismatch_reported(self): ...         # db_set content_sha256 to another value → sha256_ok False
def test_latin1_body_decoded(self): ...
def test_render_needs_read_permission(self): ...  # outsider → PermissionError
def test_render_after_communication_deleted(self): ...
def test_thread_orders_and_shows_gap(self):
	# A (archived) ← B (not archived, original kept) ← C (archived)
	items = thread.thread(c_doc)
	self.assertEqual([i["kind"] for i in items], ["mail", "gap", "mail"])
	self.assertEqual(items[1]["communication"], comm_b.name)
```

- [ ] **Step 2: Run and see them fail** — `--module docarchive.tests.test_mail_viewer`
- [ ] **Step 3: Implement `viewer.py`, `thread.py`**
- [ ] **Step 4: Run `test_mail_viewer`** — Expected: PASS
- [ ] **Step 5: Commit** — `feat: mail viewer and thread api`

---

### Task 8: Desk UI, translations and README

**Files:**
- Create: `docarchive/public/js/communication.js`, `docarchive/public/js/communication_list.js`, `docarchive/public/js/mail_viewer.js`
- Modify: `docarchive/public/js/docarchive.bundle.js` (import `./mail_viewer`), `docarchive/hooks.py` (`doctype_js = {"Communication": "public/js/communication.js"}`, `doctype_list_js = {"Communication": "public/js/communication_list.js"}`), `docarchive/document_archive/doctype/business_document/business_document.js`, `docarchive/public/js/documents_panel.js` (thread action on correspondence rows)
- Modify: `docarchive/translations/de.csv`, `README.md`
- Test: `docarchive/tests/test_translations.py` (existing), manual check in the desk

**Interfaces:**
- Consumes: `archiving.status`, `archiving.archive_communications`, `archiving.discard`, `viewer.render`, `viewer.attachment`, `thread.thread`, `docarchive.upload_documents` (existing, extended with optional `source_doctype`, `source_name`, `document_date` defaults; `documents.create_documents` gets matching optional parameters and sets them with `flags.archive_write` only for `source_doctype == "Business Document"` the user can read).
- Produces:
  - Communication form: button "Archive" (disabled with `reason` when there is no original; warning dialog when `automated` or `internal`), and a link "Archived as {0}" when `business_document` is set.
  - Communication list: bulk action "Archive" that reports refused mails in one message.
  - `docarchive.show_mail(name)` dialog: headers, body in `<iframe sandbox>` (no `allow-scripts`) using `srcdoc`, button "Load images" that copies `data-remote-src` to `src`, attachment list with download links and "File as document", integrity line ("Checksum matches" / "Checksum does not match").
  - `docarchive.show_thread(name)` dialog: the thread items with direction, date, sender; gaps with an "Archive" button when `communication` is set.
  - Business Document form: buttons "Show e-mail" and "Show thread" for correspondence documents, "Discard" while archiving has not started; list view bulk action "Discard" (confirm dialog).
  - README: a "Correspondence" section covering account settings, modes, automated and internal mails, cleanup, the viewer, and `docarchive.mail.capture(communication, raw, origin)` for host apps.

- [ ] **Step 1: Write the JS and the README section, add every new string to `de.csv`**
- [ ] **Step 2: Run `test_translations`** — Expected: PASS
- [ ] **Step 3: Build and check in the desk** — `bench build --app docarchive`; with a Manual-mode account, receive a test mail, archive it from the Communication form, open "Show e-mail" (script stripped, images blocked until clicked, attachment downloads), open "Show thread", discard a second unarchived document.
- [ ] **Step 4: Run the full suite** — `bench --site docarchive.test run-tests --app docarchive` — Expected: all PASS
- [ ] **Step 5: Commit** — `feat: desk ui for archiving and reading correspondence`
