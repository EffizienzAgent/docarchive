import hashlib
import os
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import content, mail
from docarchive.mail import archiving, cleanup, headers, originals
from docarchive.tests.factories import (
	EML,
	eml,
	make_communication,
	make_email_account,
	make_mail_settings,
	make_own_company,
	make_settings,
	store_mail_inline,
)

SCHEDULE = originals.schedule


class TestMailCapture(IntegrationTestCase):
	def setUp(self):
		store_mail_inline(self)
		make_settings()
		self.account = make_email_account().name
		make_mail_settings(self.account, mode="Manual", internal_domains="acme.test")
		self.comm = make_communication(self.account)

	def value(self, name, field):
		return frappe.db.get_value("Mail Original", name, field)

	def test_capture_stores_exact_bytes(self):
		name = mail.capture(self.comm, EML, "frappe-receive")
		original = frappe.get_doc("Mail Original", name)
		self.assertEqual(content.read_bytes(original.file), EML)
		self.assertEqual(original.sha256, hashlib.sha256(EML).hexdigest())
		self.assertEqual(original.size, len(EML))
		self.assertEqual(original.origin, "frappe-receive")
		file_doc = frappe.get_doc("File", {"file_url": original.file})
		self.assertEqual((file_doc.is_private, file_doc.attached_to_name), (1, name))
		self.assertTrue(file_doc.file_name.endswith(".eml"))

	def test_headers_and_direction(self):
		raw = eml(**{"In-Reply-To": "<a@x.test>", "References": "<r1@x.test> <r2@x.test>"})
		name = mail.capture(self.comm, raw, "t")
		original = frappe.get_doc("Mail Original", name)
		self.assertEqual(original.direction, "Incoming")
		self.assertEqual(original.in_reply_to, "a@x.test")
		self.assertEqual(original.mail_references, "r1@x.test r2@x.test")
		self.assertEqual(original.thread_key, "r1@x.test")
		self.assertEqual(original.sender, "client@example.test")
		self.assertEqual(original.subject, "Offer")
		self.assertTrue(original.message_id.endswith("@example.test"))
		sent = make_communication(self.account, "Sent")
		self.assertEqual(self.value(mail.capture(sent, EML, "t"), "direction"), "Outgoing")

	def test_str_raw_is_stored_as_utf8(self):
		name = mail.capture(self.comm, EML.decode(), "test")
		self.assertEqual(content.read_bytes(self.value(name, "file")), EML)

	def test_encoded_subject_is_decoded(self):
		raw = eml(Subject="=?utf-8?q?Angebot_f=C3=BCr_M=C3=BCller?=")
		self.assertEqual(self.value(mail.capture(self.comm, raw, "t"), "subject"), "Angebot für Müller")

	def test_without_message_id_thread_key_is_hash(self):
		raw = EML.replace(b"Message-ID", b"X-Other")
		name = mail.capture(self.comm, raw, "t")
		self.assertTrue(self.value(name, "thread_key").startswith("sha256:"))

	def test_automated_markers(self):
		for header in (
			{"List-Unsubscribe": "<mailto:x@y>"},
			{"Auto-Submitted": "auto-replied"},
			{"Precedence": "bulk"},
		):
			self.assertTrue(headers.parse(eml(**header)).automated)
		self.assertFalse(headers.parse(eml(**{"Auto-Submitted": "no"})).automated)
		self.assertFalse(headers.parse(EML).automated)

	def test_internal_detection(self):
		domains = {"acme.test"}
		internal = headers.parse(eml(From="a@acme.test", To="b@acme.test", Cc="C <c@ACME.test>"))
		self.assertTrue(headers.is_internal(internal, domains))
		mixed = headers.parse(eml(From="a@acme.test", To="b@acme.test, x@example.test"))
		self.assertFalse(headers.is_internal(mixed, domains))

	def test_internal_flag_is_stored(self):
		name = mail.capture(self.comm, eml(From="a@acme.test", To="b@acme.test"), "t")
		self.assertEqual(self.value(name, "internal"), 1)

	def test_bad_headers_do_not_raise(self):
		parsed = headers.parse(b"\xff\xfe not a mail")
		self.assertEqual(parsed.references, [])

	def test_off_account_is_not_captured(self):
		off = make_email_account().name
		make_mail_settings(off, mode="Off")
		self.assertIsNone(mail.capture(make_communication(off), EML, "t"))
		self.assertIsNone(mail.capture(make_communication(None), EML, "t"))

	def test_second_capture_returns_existing(self):
		first = mail.capture(self.comm, EML, "t")
		self.assertEqual(mail.capture(self.comm, eml(), "t"), first)
		self.assertEqual(frappe.db.count("Mail Original", {"communication": self.comm.name}), 1)

	def test_failure_does_not_raise(self):
		with patch("docarchive.mail.originals._pend", side_effect=Exception("boom")):
			self.assertIsNone(mail.capture(self.comm, EML, "t"))
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": self.comm.name}))

	def test_failing_write_keeps_a_failed_capture(self):
		with (
			patch("docarchive.mail.originals.store_file", side_effect=Exception("boom")),
			patch("frappe.log_error") as log_error,
		):
			name = mail.capture(self.comm, EML, "t")
		status, attempts, file, error = frappe.db.get_value(
			"Mail Original", name, ["status", "attempts", "file", "error"]
		)
		self.assertEqual((status, attempts, file), ("Failed", 1, None))
		self.assertIn("boom", error)
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": name}))
		log_error.assert_called_with(title="docarchive mail capture")

	def test_auto_archive_called_after_capture(self):
		with patch("docarchive.mail.originals.archiving.auto_archive") as auto:
			name = mail.capture(self.comm, EML, "t")
		auto.assert_called_once_with(name)

	def test_long_identifiers_are_captured(self):
		message_id = "m" * 190 + "@example.test"
		reference = "r" * 600
		raw = eml(**{"Message-ID": f"<{message_id}>", "References": f"<{reference}> <b@x.test>"})
		name = mail.capture(self.comm, raw, "t")
		original = frappe.get_doc("Mail Original", name)
		self.assertEqual(original.message_id, message_id)
		self.assertEqual(original.thread_key, reference[:500])
		self.assertEqual(content.read_bytes(original.file), raw)

	def test_unencodable_str_does_not_raise(self):
		self.assertIsNone(mail.capture(self.comm, "Subject: x\n\n\ud800", "t"))

	def test_failing_rollback_and_log_do_not_raise(self):
		with (
			patch("docarchive.mail.originals._pend", side_effect=Exception("boom")),
			patch("frappe.db.rollback", side_effect=Exception("r")),
			patch("frappe.log_error", side_effect=Exception("l")),
		):
			self.assertIsNone(mail.capture(self.comm, EML, "t"))

	def test_failing_auto_archive_keeps_capture_and_rolls_back_its_writes(self):
		def partial(name):
			frappe.db.set_value("Mail Original", name, "origin", "partial")
			raise Exception("boom")

		with patch("docarchive.mail.originals.archiving.auto_archive", side_effect=partial):
			name = mail.capture(self.comm, EML, "t")
		self.assertEqual(self.value(name, "origin"), "t")

	def test_auto_archive_not_called_for_existing(self):
		mail.capture(self.comm, EML, "t")
		with patch("docarchive.mail.originals.archiving.auto_archive") as auto:
			mail.capture(self.comm, EML, "t")
		auto.assert_not_called()


class TestMailStoreJob(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)
		self.account = make_email_account().name
		make_mail_settings(self.account, mode="Automatic", internal_domains="acme.test")
		self.comm = make_communication(self.account)
		self.scheduled = []
		self.enterContext(patch("docarchive.mail.originals.schedule", side_effect=self.scheduled.append))

	def pending(self, raw=EML):
		return mail.capture(self.comm, raw, "t")

	def row(self, name, *fields):
		return frappe.db.get_value("Mail Original", name, list(fields))

	def test_capture_writes_the_file_and_leaves_parsing_to_the_job(self):
		name = self.pending()
		status, file, direction, sha = self.row(name, "status", "file", "direction", "sha256")
		self.assertEqual((status, direction, sha), ("Pending", "Incoming", None))
		self.assertEqual(content.read_bytes(file), EML)
		file_doc = frappe.get_doc("File", {"file_url": file})
		self.assertEqual((file_doc.is_private, file_doc.attached_to_name), (1, name))
		self.assertTrue(file_doc.file_name.endswith(".eml"))
		self.assertEqual(self.scheduled, [name])
		self.assertFalse(frappe.db.exists("Business Document", {"source_name": self.comm.name}))

	def test_capture_keeps_nothing_in_the_site_folder(self):
		folder = frappe.get_site_path("private", "docarchive")
		before = set(os.listdir(folder)) if os.path.isdir(folder) else set()
		self.pending()
		after = set(os.listdir(folder)) if os.path.isdir(folder) else set()
		self.assertEqual(after, before)

	def test_schedule_enqueues_deduplicated_long_job_after_commit(self):
		SCHEDULE("MO-1")
		callback = frappe.db.after_commit._functions.pop()
		with patch("frappe.enqueue") as enqueue:
			callback()
		enqueue.assert_called_once_with(
			"docarchive.mail.originals.store",
			queue="long",
			job_id="docarchive-mail-original-MO-1",
			deduplicate=True,
			original="MO-1",
		)

	def test_store_job_stores_and_auto_archives(self):
		name = self.pending()
		file = self.row(name, "file")
		originals.store(name)
		original = frappe.get_doc("Mail Original", name)
		self.assertEqual((original.status, original.sha256), ("Stored", hashlib.sha256(EML).hexdigest()))
		self.assertEqual(original.file, file)
		self.assertEqual(content.read_bytes(original.file), EML)
		self.assertEqual(original.subject, "Offer")
		self.assertTrue(original.business_document)

	def test_store_job_writes_no_second_copy(self):
		name = self.pending()
		with (
			patch("docarchive.mail.originals.store_file") as store_file,
			patch("s3storage.backend.write_file") as write_file,
		):
			originals.store(name)
		store_file.assert_not_called()
		write_file.assert_not_called()
		document = self.row(name, "business_document")
		self.assertEqual(frappe.db.get_value("Business Document", document, "file"), self.row(name, "file"))

	def test_store_job_is_idempotent(self):
		name = self.pending()
		originals.store(name)
		with patch("docarchive.mail.originals.store_file") as store_file:
			originals.store(name)
		store_file.assert_not_called()
		self.assertEqual(mail.capture(self.comm, eml(), "t"), name)
		self.assertEqual(frappe.db.count("File", {"attached_to_name": name}), 1)

	def test_failures_are_retried_then_marked_failed(self):
		name = self.pending()
		with (
			patch("docarchive.mail.originals.headers.parse", side_effect=Exception("boom")),
			patch("frappe.log_error") as log_error,
		):
			for attempt in range(1, originals.MAX_ATTEMPTS + 1):
				originals.store(name)
				status = "Failed" if attempt == originals.MAX_ATTEMPTS else "Pending"
				self.assertEqual(self.row(name, "status", "attempts"), (status, attempt))
		log_error.assert_called_with(title="docarchive mail capture")
		self.assertEqual(content.read_bytes(self.row(name, "file")), EML)
		self.assertEqual(
			archiving.refusal(name), "Storing the original of this e-mail failed. See the Error Log."
		)
		self.scheduled.clear()
		self.assertGreaterEqual(originals.retry_failed(), 1)
		self.assertIn(name, self.scheduled)
		self.assertEqual(self.row(name, "status", "attempts"), ("Pending", 0))
		originals.store(name)
		self.assertEqual(self.row(name, "status"), "Stored")

	def test_missing_file_fails_at_once(self):
		name = self.pending()
		with patch("docarchive.mail.originals.content.read_bytes", return_value=None):
			originals.store(name)
		status, attempts, error = self.row(name, "status", "attempts", "error")
		self.assertEqual((status, attempts), ("Failed", 1))
		self.assertIn("missing", error)

	def test_original_without_file_fails_at_once(self):
		name = self.pending()
		frappe.db.set_value("Mail Original", name, "file", None)
		originals.store(name)
		status, error = self.row(name, "status", "error")
		self.assertEqual(status, "Failed")
		self.assertIn("not kept", error)

	def test_failed_row_shows_the_reason(self):
		name = self.pending()
		with patch("docarchive.mail.originals.headers.parse", side_effect=Exception("File too big")):
			originals.store(name)
		self.assertIn("File too big", self.row(name, "error"))
		originals.store(name)
		self.assertIsNone(self.row(name, "error"))

	def test_deleted_email_queue_fails_at_once(self):
		sent = make_communication(self.account, "Sent")
		name = originals._pend(sent, "frappe-send", email_queue="missing-queue")
		originals.store(name)
		self.assertEqual(self.row(name, "status", "attempts"), ("Failed", 1))

	def test_pending_original_cannot_be_archived_yet(self):
		name = self.pending()
		reason = "The original of this e-mail is still being stored. Please try again in a moment."
		self.assertEqual(archiving.refusal(name), reason)
		with self.assertRaises(frappe.ValidationError):
			archiving.archive(name)

	def test_fresh_bytes_revive_a_failed_original(self):
		name = self.pending()
		frappe.db.set_value("Mail Original", name, {"status": "Failed", "attempts": 5, "error": "x"})
		self.scheduled.clear()
		fresh = eml()
		self.assertEqual(mail.capture(self.comm, fresh, "t"), name)
		self.assertEqual(self.row(name, "status", "attempts", "error"), ("Pending", 0, None))
		self.assertEqual(self.scheduled, [name])
		self.assertEqual(frappe.db.count("File", {"attached_to_name": name}), 1)
		self.assertEqual(content.read_bytes(self.row(name, "file")), fresh)
		originals.store(name)
		self.assertEqual(self.row(name, "status", "sha256"), ("Stored", hashlib.sha256(fresh).hexdigest()))

	def test_revive_without_a_previous_file(self):
		name = self.pending()
		frappe.delete_doc("File", frappe.db.get_value("File", {"attached_to_name": name}), force=True)
		frappe.db.set_value("Mail Original", name, {"status": "Failed", "file": None})
		mail.capture(self.comm, EML, "t")
		self.assertEqual(content.read_bytes(self.row(name, "file")), EML)

	def test_failing_revive_stays_failed_with_the_old_file(self):
		name = self.pending()
		file = self.row(name, "file")
		frappe.db.set_value("Mail Original", name, {"status": "Failed", "error": "x"})
		with (
			patch("docarchive.mail.originals.store_file", side_effect=Exception("S3 down")),
			patch("frappe.log_error"),
		):
			self.assertEqual(mail.capture(self.comm, eml(), "t"), name)
		self.assertEqual(self.row(name, "status", "file"), ("Failed", file))
		self.assertIn("S3 down", self.row(name, "error"))

	def test_unlinked_original_is_retried(self):
		name = self.pending()
		frappe.db.set_value("Mail Original", name, {"communication": None, "status": "Failed"})
		self.assertGreaterEqual(originals.retry_failed(), 1)
		self.assertIn(name, self.scheduled)
		with patch("docarchive.mail.originals.enqueue") as enqueue:
			originals.retry_pending()
		enqueue.assert_any_call(name)
		originals.store(name)
		self.assertEqual(self.row(name, "status"), "Stored")

	def test_refusal_after_a_failed_attempt_says_it_is_retried(self):
		name = self.pending()
		frappe.db.set_value("Mail Original", name, "attempts", 1)
		self.assertEqual(
			archiving.refusal(name),
			"Storing the original of this e-mail failed. It will be retried automatically.",
		)

	def test_cleanup_retries_pending(self):
		name = self.pending()
		with patch("docarchive.mail.originals.enqueue") as enqueue:
			cleanup.run()
		enqueue.assert_any_call(name)

	def test_deleting_pending_original_removes_its_file(self):
		name = self.pending()
		frappe.delete_doc("Mail Original", name, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": name}))


class TestSpoolMigration(IntegrationTestCase):
	def setUp(self):
		make_settings()
		self.account = make_email_account().name
		make_mail_settings(self.account, mode="Manual")
		self.schedule = self.enterContext(patch("docarchive.mail.originals.schedule"))

	def legacy(self, raw=None):
		name = originals._pend(make_communication(self.account), "t")
		if raw is not None:
			path = frappe.get_site_path("private", "docarchive", "mail-spool", f"{name}.eml")
			os.makedirs(os.path.dirname(path), exist_ok=True)
			with open(path, "wb") as f:
				f.write(raw)
			self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
		return name

	def test_readable_spool_becomes_the_file_and_is_removed(self):
		from docarchive.patches import move_mail_spool

		spooled, lost = self.legacy(EML), self.legacy()
		move_mail_spool.execute()
		self.assertEqual(content.read_bytes(frappe.db.get_value("Mail Original", spooled, "file")), EML)
		self.assertEqual(frappe.db.get_value("Mail Original", spooled, "status"), "Pending")
		self.schedule.assert_any_call(spooled)
		frappe.db.after_commit.run()
		path = frappe.get_site_path("private", "docarchive", "mail-spool", f"{spooled}.eml")
		self.assertFalse(os.path.exists(path))
		status, error = frappe.db.get_value("Mail Original", lost, ["status", "error"])
		self.assertEqual(status, "Failed")
		self.assertIn("spool", error)
