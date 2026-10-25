import base64
import hashlib
import os
import pickle
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from s3storage import storage
from s3storage.tests.fake_s3 import FakeClientError, FakeS3

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
	resume_mail_writes,
	store_mail_inline,
)

SCHEDULE = originals.schedule
S3_CONFIG = {
	"s3_files_endpoint": "https://s3.example.com",
	"s3_files_region": "eu-central-1",
	"s3_files_bucket": "files",
	"s3_files_access_key": "key",
	"s3_files_secret_key": "secret",
}
SPOOL = ("private", "docarchive", "mail-spool")


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

	def test_failing_write_keeps_the_bytes_in_the_database(self):
		with (
			patch("docarchive.mail.originals.new_file", side_effect=Exception("boom")),
			patch("frappe.log_error") as log_error,
		):
			name = mail.capture(self.comm, EML, "t")
		status, attempts, file, fallback = frappe.db.get_value(
			"Mail Original", name, ["status", "attempts", "file", "raw_fallback"]
		)
		self.assertEqual((status, attempts, file), ("Pending", 1, None))
		self.assertEqual(base64.b64decode(fallback), EML)
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": name}))
		log_error.assert_called_with(title="docarchive mail capture")

	def test_auto_archive_called_after_capture(self):
		with patch("docarchive.mail.originals.archiving.auto_archive") as auto:
			name = mail.capture(self.comm, EML, "t")
		auto.assert_called_once_with(name, EML)

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
		resume_mail_writes(self)

	def pending(self, raw=EML):
		return mail.capture(self.comm, raw, "t")

	def failing_write(self, error=None):
		return patch("docarchive.mail.originals.new_file", side_effect=error or OSError("S3 down"))

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
			patch("docarchive.mail.originals.new_file") as new_file,
			patch("s3storage.backend.write_file") as write_file,
		):
			originals.store(name)
		new_file.assert_not_called()
		write_file.assert_not_called()
		document = self.row(name, "business_document")
		self.assertEqual(frappe.db.get_value("Business Document", document, "file"), self.row(name, "file"))

	def test_store_job_is_idempotent(self):
		name = self.pending()
		originals.store(name)
		with patch("docarchive.mail.originals.new_file") as new_file:
			originals.store(name)
		new_file.assert_not_called()
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
		self.assertEqual(frappe.db.count("File", {"attached_to_name": name}), 2)
		self.assertEqual(content.read_bytes(self.row(name, "file")), fresh)
		originals.store(name)
		self.assertEqual(self.row(name, "status", "sha256"), ("Stored", hashlib.sha256(fresh).hexdigest()))
		self.assertEqual(frappe.db.count("File", {"attached_to_name": name}), 1)
		self.assertEqual(content.read_bytes(self.row(name, "file")), fresh)

	def test_revive_with_the_same_bytes_keeps_one_file(self):
		name = self.pending()
		frappe.db.set_value("Mail Original", name, "status", "Failed")
		mail.capture(self.comm, EML, "t")
		originals.store(name)
		self.assertEqual(frappe.db.count("File", {"attached_to_name": name}), 1)
		self.assertEqual(content.read_bytes(self.row(name, "file")), EML)

	def test_revive_without_a_previous_file(self):
		name = self.pending()
		frappe.delete_doc("File", frappe.db.get_value("File", {"attached_to_name": name}), force=True)
		frappe.db.set_value("Mail Original", name, {"status": "Failed", "file": None})
		mail.capture(self.comm, EML, "t")
		self.assertEqual(content.read_bytes(self.row(name, "file")), EML)

	def test_revive_while_storage_is_down_keeps_the_old_file_until_stored(self):
		name = self.pending()
		old = self.row(name, "file")
		frappe.db.set_value("Mail Original", name, {"status": "Failed", "error": "x"})
		fresh = eml()
		with self.failing_write(), patch("frappe.log_error"):
			self.assertEqual(mail.capture(self.comm, fresh, "t"), name)
		status, file, fallback = self.row(name, "status", "file", "raw_fallback")
		self.assertEqual((status, file), ("Pending", None))
		self.assertEqual(base64.b64decode(fallback), fresh)
		self.assertTrue(frappe.db.exists("File", {"file_url": old, "attached_to_name": name}))
		resume_mail_writes(self)
		originals.store(name)
		self.assertEqual(self.row(name, "status", "raw_fallback"), ("Stored", None))
		self.assertEqual(frappe.db.count("File", {"attached_to_name": name}), 1)
		self.assertEqual(content.read_bytes(self.row(name, "file")), fresh)

	def test_failed_revive_keeps_the_old_file(self):
		name = self.pending()
		old = self.row(name, "file")
		frappe.db.set_value("Mail Original", name, {"status": "Failed", "error": "x"})
		with self.failing_write(), patch.object(originals, "FALLBACK_LIMIT", 10), patch("frappe.log_error"):
			mail.capture(self.comm, eml(), "t")
		self.assertEqual(self.row(name, "status", "file"), ("Failed", old))
		self.assertIn("10 MB", self.row(name, "error"))

	def test_write_failure_keeps_the_bytes_for_the_job(self):
		with self.failing_write(), patch("frappe.log_error") as log_error:
			name = self.pending()
		status, file, fallback = self.row(name, "status", "file", "raw_fallback")
		self.assertEqual((status, file), ("Pending", None))
		self.assertEqual(base64.b64decode(fallback), EML)
		self.assertEqual(self.scheduled, [name])
		log_error.assert_called_with(title="docarchive mail capture")
		self.assertTrue(frappe.cache.get_value(originals.PAUSE_KEY))
		resume_mail_writes(self)
		originals.store(name)
		self.assertEqual(self.row(name, "status", "raw_fallback"), ("Stored", None))
		self.assertEqual(content.read_bytes(self.row(name, "file")), EML)
		self.assertTrue(self.row(name, "business_document"))

	def test_paused_writes_skip_the_file_storage(self):
		frappe.cache.set_value(originals.PAUSE_KEY, 1, expires_in_sec=300)
		with patch("docarchive.mail.originals.new_file") as new_file:
			name = self.pending()
		new_file.assert_not_called()
		self.assertEqual(base64.b64decode(self.row(name, "raw_fallback")), EML)

	def test_job_keeps_the_bytes_while_storage_is_down(self):
		with self.failing_write(), patch("frappe.log_error"):
			name = self.pending()
			originals.store(name)
		status, attempts, fallback = self.row(name, "status", "attempts", "raw_fallback")
		self.assertEqual((status, attempts), ("Pending", 1))
		self.assertEqual(base64.b64decode(fallback), EML)

	def test_large_mail_fails_when_storage_is_down(self):
		with self.failing_write(), patch.object(originals, "FALLBACK_LIMIT", 10), patch("frappe.log_error"):
			name = self.pending()
		status, file, fallback, error = self.row(name, "status", "file", "raw_fallback", "error")
		self.assertEqual((status, file, fallback), ("Failed", None, None))
		self.assertIn("10 MB", error)
		self.assertEqual(self.scheduled, [])

	def test_too_large_file_fails_without_fallback_or_pause(self):
		messages = len(frappe.local.message_log)
		with patch("frappe.core.api.file.get_max_file_size", return_value=10), patch("frappe.log_error"):
			name = self.pending()
		status, fallback, error = self.row(name, "status", "raw_fallback", "error")
		self.assertEqual((status, fallback), ("Failed", None))
		self.assertIn("maximum allowed size", error)
		self.assertNotIn("<", error)
		self.assertEqual(len(frappe.local.message_log), messages)
		self.assertFalse(frappe.cache.get_value(originals.PAUSE_KEY))

	def test_pause_set_by_another_process_is_seen(self):
		self.assertFalse(originals._paused())
		frappe.cache.set(frappe.cache.make_key(originals.PAUSE_KEY), pickle.dumps(1))
		self.assertTrue(originals._paused())

	def test_expired_pause_ends_in_the_same_process(self):
		originals._pause()
		self.assertTrue(originals._paused())
		frappe.cache.delete(frappe.cache.make_key(originals.PAUSE_KEY))
		self.assertFalse(originals._paused())

	def test_large_mail_is_written_while_paused(self):
		originals._pause()
		with patch.object(originals, "FALLBACK_LIMIT", 10):
			name = self.pending()
		self.assertEqual(self.row(name, "status", "raw_fallback"), ("Pending", None))
		self.assertEqual(content.read_bytes(self.row(name, "file")), EML)

	def test_failing_fallback_write_keeps_a_failed_original(self):
		def partial(original, raw):
			frappe.db.set_value("Mail Original", original.name, "origin", "partial")
			raise Exception("packet too large")

		with (
			self.failing_write(),
			patch.object(originals, "_keep", side_effect=partial),
			patch("frappe.log_error"),
		):
			name = self.pending()
		status, origin, error = self.row(name, "status", "origin", "error")
		self.assertEqual((status, origin), ("Failed", "t"))
		self.assertIn("packet too large", error)

	def test_form_load_leaves_out_the_raw_fallback(self):
		from frappe.desk.form.load import getdoc

		with self.failing_write(), patch("frappe.log_error"):
			name = self.pending()
		getdoc("Mail Original", name)
		self.assertIsNone(frappe.response.docs[0].get("raw_fallback"))
		self.assertTrue(self.row(name, "raw_fallback"))

		with (
			patch.object(originals, "FALLBACK_LIMIT", 10),
			self.failing_write(),
			patch("frappe.db.set_value", side_effect=Exception("db")),
			patch("frappe.log_error"),
		):
			self.assertTrue(self.pending())

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
		resume_mail_writes(self)

	def path(self, name):
		return frappe.get_site_path(*SPOOL, f"{name}.eml")

	def legacy(self, raw=None, status="Pending"):
		name = originals._pend(make_communication(self.account), "t")
		frappe.db.set_value(
			"Mail Original", name, {"status": status, "attempts": 5 if status == "Failed" else 0}
		)
		if raw is not None:
			path = self.path(name)
			os.makedirs(os.path.dirname(path), exist_ok=True)
			with open(path, "wb") as f:
				f.write(raw)
			self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
		return name

	def row(self, name, *fields):
		return frappe.db.get_value("Mail Original", name, list(fields))

	def migrate(self):
		from docarchive.patches import move_mail_spool

		move_mail_spool.execute()
		frappe.db.after_commit.run()

	def test_readable_spool_becomes_the_file_and_is_removed(self):
		spooled, lost = self.legacy(EML), self.legacy()
		self.migrate()
		self.assertEqual(content.read_bytes(self.row(spooled, "file")), EML)
		self.assertEqual(self.row(spooled, "status"), "Pending")
		self.schedule.assert_any_call(spooled)
		self.assertFalse(os.path.exists(self.path(spooled)))
		status, error = self.row(lost, "status", "error")
		self.assertEqual(status, "Failed")
		self.assertIn("spool", error)

	def test_failed_original_with_spool_is_revived(self):
		name = self.legacy(EML, status="Failed")
		self.migrate()
		self.assertEqual(self.row(name, "status", "attempts"), ("Pending", 0))
		self.assertEqual(content.read_bytes(self.row(name, "file")), EML)
		self.schedule.assert_any_call(name)

	def test_failed_original_without_spool_keeps_its_error(self):
		name = self.legacy(status="Failed")
		frappe.db.set_value("Mail Original", name, "error", "rebuild window passed")
		self.migrate()
		self.assertEqual(self.row(name, "status", "error"), ("Failed", "rebuild window passed"))

	def test_without_spool_folder(self):
		folder = frappe.get_site_path(*SPOOL)
		if os.path.isdir(folder) and not os.listdir(folder):
			os.rmdir(folder)
		lost = self.legacy()
		self.migrate()
		self.assertEqual(self.row(lost, "status"), "Failed")

	def test_oversized_spool_stays_on_disk(self):
		name, kept = self.legacy(b"x" * 5000), self.legacy(EML)
		with patch("frappe.core.api.file.get_max_file_size", return_value=2000), patch("frappe.log_error"):
			self.migrate()
		self.assertEqual(self.row(name, "status"), "Failed")
		self.assertIn("maximum allowed size", self.row(name, "error"))
		self.assertTrue(os.path.exists(self.path(name)))
		self.assertFalse(os.path.exists(self.path(kept)))

	def test_one_broken_row_does_not_stop_the_migration(self):
		broken, fine = self.legacy(EML), self.legacy(eml())
		real = originals._write

		def write(original, raw):
			if original.name == broken:
				raise Exception("boom")
			return real(original, raw)

		with patch("docarchive.mail.originals._write", side_effect=write), patch("frappe.log_error") as log:
			self.migrate()
		self.assertEqual(self.row(broken, "status"), "Failed")
		self.assertIn("boom", self.row(broken, "error"))
		self.assertTrue(os.path.exists(self.path(broken)))
		self.assertEqual(self.row(fine, "status"), "Pending")
		log.assert_called()


class TestMailCaptureOnS3(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)
		self.account = make_email_account().name
		make_mail_settings(self.account, mode="Automatic", internal_domains="acme.test")
		self.comm = make_communication(self.account)
		self.s3 = FakeS3()
		self.enterContext(patch.dict(frappe.local.conf, S3_CONFIG))
		self.enterContext(patch.object(storage, "client", return_value=self.s3))
		self.enterContext(patch("docarchive.mail.originals.schedule"))
		resume_mail_writes(self)

	def key(self, name):
		return storage.key_for(frappe.db.get_value("Mail Original", name, "file"))

	def row(self, name, *fields):
		return frappe.db.get_value("Mail Original", name, list(fields))

	def test_capture_writes_only_to_object_storage(self):
		name = mail.capture(self.comm, EML, "t")
		file = self.row(name, "file")
		self.assertEqual(self.s3.content(self.key(name)), EML)
		self.assertFalse(os.path.exists(storage.local_path(file)))
		self.s3.calls.clear()
		originals.store(name)
		self.assertIn("get_object", self.s3.calls)
		self.assertEqual(self.row(name, "status"), "Stored")

	def test_archiving_uploads_nothing(self):
		name = mail.capture(self.comm, EML, "t")
		self.s3.calls.clear()
		originals.store(name)
		self.assertNotIn("put_object", self.s3.calls)
		document = self.row(name, "business_document")
		self.assertEqual(frappe.db.get_value("Business Document", document, "file"), self.row(name, "file"))

	def test_revive_deletes_the_old_object_only_once_stored(self):
		name = mail.capture(self.comm, eml(), "t")
		old = self.key(name)
		frappe.db.set_value("Mail Original", name, "status", "Failed")
		fresh = eml()
		mail.capture(self.comm, fresh, "t")
		self.assertIn(old, self.s3.keys())
		originals.store(name)
		self.assertNotIn(old, self.s3.keys())
		self.assertEqual(self.s3.content(self.key(name)), fresh)

	def test_put_failure_falls_back_to_the_database(self):
		with (
			patch.object(self.s3, "put_object", side_effect=FakeClientError("InternalError")) as put,
			patch("frappe.log_error"),
		):
			name = mail.capture(self.comm, EML, "t")
			other = mail.capture(make_communication(self.account), eml(), "t")
		put.assert_called_once()
		self.assertEqual(self.s3.keys(), [])
		for each in (name, other):
			self.assertEqual(self.row(each, "status", "file"), ("Pending", None))
			self.assertTrue(self.row(each, "raw_fallback"))
		resume_mail_writes(self)
		originals.store(name)
		self.assertEqual(self.row(name, "status", "raw_fallback"), ("Stored", None))
		self.assertEqual(self.s3.content(self.key(name)), EML)

	def test_failing_link_after_upload_removes_the_object(self):
		with patch.object(originals, "_link", side_effect=Exception("late")), patch("frappe.log_error"):
			name = mail.capture(self.comm, eml(), "t")
		self.assertEqual(self.s3.keys(), [])
		self.assertTrue(self.row(name, "raw_fallback"))

	def test_failing_store_after_upload_removes_the_object(self):
		frappe.cache.set_value(originals.PAUSE_KEY, 1, expires_in_sec=300)
		name = mail.capture(self.comm, eml(), "t")
		resume_mail_writes(self)
		with (
			patch.object(originals, "_drop_replaced", side_effect=Exception("late")),
			patch("frappe.log_error"),
		):
			originals.store(name)
		self.assertEqual(self.s3.keys(), [])
		self.assertEqual(self.row(name, "status", "attempts"), ("Pending", 1))
		self.assertTrue(self.row(name, "raw_fallback"))

	def test_failure_after_upload_removes_the_object(self):
		with (
			patch("frappe.core.doctype.file.file.File.after_insert", side_effect=Exception("late")),
			patch("frappe.log_error"),
		):
			name = mail.capture(self.comm, eml(), "t")
		self.assertEqual(self.s3.keys(), [])
		self.assertTrue(self.row(name, "raw_fallback"))
