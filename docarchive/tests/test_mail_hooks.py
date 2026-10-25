from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.email.receive import InboundMail, SentEmailInInboxError
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from docarchive import content
from docarchive.mail import archiving, originals
from docarchive.tests.factories import (
	eml,
	make_business_document,
	make_communication,
	make_email_account,
	make_mail_settings,
	make_own_company,
	make_settings,
	store_mail_inline,
	unique,
)


class TestMailHooks(IntegrationTestCase):
	def setUp(self):
		store_mail_inline(self)
		make_settings(default_own_company=make_own_company().name)
		self.email_id = f"{unique('info')}@acme.test"
		self.account = make_email_account(
			self.email_id, enable_outgoing=1, smtp_server="smtp.example.test", smtp_port=587
		)
		make_mail_settings(self.account.name, mode="Manual")
		self.message_id = f"{unique('mid')}@example.test"
		self.raw = eml(To=self.email_id, **{"Message-ID": f"<{self.message_id}>"})
		patcher = patch.object(frappe.db, "commit")
		self.commit = patcher.start()
		self.addCleanup(patcher.stop)

	def receive(self, raw=None):
		account = frappe.get_doc("Email Account", self.account.name)
		mail = InboundMail(raw or self.raw, account, uid="1")
		with patch.object(EmailAccount, "get_inbound_mails", return_value=[mail]):
			inbound = account.get_inbound_mails()
		return inbound[0]

	def received(self):
		return frappe.db.get_value("Communication", {"message_id": self.message_id})

	def test_receive_captures_raw(self):
		account = frappe.get_doc("Email Account", self.account.name)
		mail = InboundMail(self.raw, account, uid="1")
		with patch.object(EmailAccount, "get_inbound_mails", return_value=[mail]):
			account.receive()
		original = frappe.get_doc("Mail Original", {"communication": self.received()})
		self.assertEqual(content.read_bytes(original.file), self.raw)
		self.assertEqual((original.origin, original.direction), ("frappe-receive", "Incoming"))

	def test_frappe_mail_origin(self):
		self.account.db_set("service", "Frappe Mail")
		comm = self.receive().process()
		self.assertEqual(
			frappe.db.get_value("Mail Original", {"communication": comm.name}, "origin"), "frappe-mail"
		)

	def test_process_return_value_unchanged(self):
		comm = self.receive().process()
		self.assertEqual(comm.name, self.received())

	def test_process_exception_propagates(self):
		raw = eml(From=self.email_id, To=self.email_id)
		with self.assertRaises(SentEmailInInboxError):
			self.receive(raw).process()

	def pending(self, comm):
		return frappe.db.get_value("Mail Original", {"communication": comm.name}, ["status", "file"])

	def test_receive_succeeds_when_capture_fails(self):
		with patch("docarchive.mail.originals._pend", side_effect=Exception("boom")):
			comm = self.receive().process()
		self.assertEqual(comm.name, self.received())
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def test_receive_succeeds_when_storing_fails(self):
		with patch("docarchive.mail.originals.store_file", side_effect=Exception("boom")):
			comm = self.receive().process()
		self.assertEqual(comm.name, self.received())
		self.assertEqual(self.pending(comm), ("Pending", None))

	def test_receive_logs_and_continues_when_capture_raises(self):
		with (
			patch("docarchive.mail.originals.capture", side_effect=Exception("boom")),
			patch("frappe.log_error") as log_error,
		):
			comm = self.receive().process()
		self.assertEqual(comm.name, self.received())
		log_error.assert_called_with(title="docarchive mail capture")

	def send(self, comm, **kwargs):
		frappe.flags.sent_mail = None
		queue = frappe.sendmail(
			recipients=["client@example.test"],
			sender=self.email_id,
			subject="Offer",
			message="Hello",
			communication=comm.name,
			delayed=True,
			**kwargs,
		)
		queue.send()

	def test_send_captures_sent_bytes(self):
		comm = make_communication(self.account.name, "Sent")
		self.send(comm)
		original = frappe.get_doc("Mail Original", {"communication": comm.name})
		self.assertTrue(frappe.flags.sent_mail)
		self.assertEqual(content.read_bytes(original.file), frappe.safe_encode(frappe.flags.sent_mail))
		self.assertEqual((original.origin, original.direction), ("frappe-send", "Outgoing"))

	def use_sent_folder(self):
		frappe.db.set_value("Email Account", self.account.name, "use_imap", 1)
		make_mail_settings(self.account.name, mode="Manual", outgoing_source="Sent Folder")

	def test_sent_folder_source_skips_capture_on_send(self):
		self.use_sent_folder()
		comm = make_communication(self.account.name, "Sent")
		self.send(comm)
		self.assertTrue(frappe.flags.sent_mail)
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def test_mailsync_hook_captures_existing_communication(self):
		self.use_sent_folder()
		comm = make_communication(self.account.name, "Sent")
		self.send(comm)
		handlers = frappe.get_hooks("mailsync_mail_read")
		self.assertTrue(handlers)
		self.commit.reset_mock()
		for method in handlers:
			frappe.get_attr(method)(
				communication=comm,
				raw=self.raw,
				account=self.account.name,
				folder="Sent",
				sent_or_received="Sent",
			)
		self.commit.assert_not_called()
		original = frappe.get_doc("Mail Original", {"communication": comm.name})
		self.assertEqual((original.origin, original.direction), ("mailsync", "Outgoing"))
		originals.from_mailsync(comm.name, self.raw)
		self.assertEqual(frappe.db.count("Mail Original", {"communication": comm.name}), 1)

	def test_mailsync_handler_never_raises(self):
		with patch("docarchive.mail.originals.capture", side_effect=Exception("boom")):
			originals.from_mailsync("missing", b"x")

	def test_send_succeeds_when_capture_fails(self):
		comm = make_communication(self.account.name, "Sent")
		with patch("docarchive.mail.originals.store_file", side_effect=Exception("boom")):
			self.send(comm)
		self.assertTrue(frappe.flags.sent_mail)
		self.assertEqual(self.pending(comm), ("Pending", None))

	def test_redacted_mail_not_captured(self):
		comm = make_communication(self.account.name, "Sent")
		self.send(comm, redact_message_after_send=1)
		self.assertTrue(frappe.flags.sent_mail)
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def queue_for(self, comm):
		return frappe.get_doc("Email Queue", {"communication": comm.name})

	def original_of(self, comm):
		return frappe.db.get_value("Mail Original", {"communication": comm.name})

	def test_send_now_commits_capture(self):
		comm = make_communication(self.account.name, "Sent")
		committed = []
		self.commit.side_effect = lambda *args, **kwargs: committed.append(bool(self.original_of(comm)))
		frappe.sendmail(
			recipients=["client@example.test"],
			sender=self.email_id,
			subject="Offer",
			message="Hello",
			communication=comm.name,
			now=True,
		)
		frappe.db.after_commit.run()
		self.assertTrue(frappe.flags.sent_mail)
		self.assertTrue(committed[-1])
		self.assertTrue(self.original_of(comm))

	def test_send_without_sending_not_captured(self):
		comm = make_communication(self.account.name, "Sent")
		self.send(comm)
		frappe.delete_doc("Mail Original", self.original_of(comm))
		self.queue_for(comm).send()
		self.assertFalse(self.original_of(comm))

	def test_newsletter_not_captured(self):
		comm = make_communication(self.account.name, "Sent")
		self.send(comm)
		frappe.delete_doc("Mail Original", self.original_of(comm))
		queue = self.queue_for(comm)
		queue.reference_doctype = "Newsletter"
		self.assertIsNone(originals.capture_sent(queue, 0))
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def test_unsent_queue_not_captured(self):
		comm = make_communication(self.account.name, "Sent")
		with patch.dict(frappe.flags, {"mute_emails": True}):
			self.send(comm)
		self.assertIsNone(originals.capture_sent(self.queue_for(comm), 0))
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def test_capture_sent_never_raises(self):
		comm = make_communication(self.account.name, "Sent")
		with patch("docarchive.mail.originals._pend", side_effect=Exception("boom")):
			self.send(comm)
		self.assertTrue(frappe.flags.sent_mail)
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def test_failing_rebuild_keeps_pending_capture(self):
		comm = make_communication(self.account.name, "Sent")
		with patch("docarchive.mail.originals.SendMailContext", side_effect=Exception("boom")):
			self.send(comm)
		self.assertTrue(frappe.flags.sent_mail)
		self.assertEqual(self.pending(comm), ("Pending", None))

	def queued_send(self, comm):
		scheduled = []
		with patch("docarchive.mail.originals.schedule", side_effect=scheduled.append):
			self.send(comm)
		[name] = scheduled
		self.addCleanup(originals.remove_spool, name)
		return name

	def stored(self, name):
		originals.store(name)
		return frappe.get_doc("Mail Original", name)

	def test_rebuild_records_time_in_original_and_meta(self):
		before = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
		original = self.stored(self.queued_send(make_communication(self.account.name, "Sent")))
		self.assertEqual(original.status, "Stored")
		self.assertGreaterEqual(get_datetime(original.rebuilt_at), before)
		self.assertLess(get_datetime(original.rebuilt_at), before + timedelta(minutes=5))
		document = frappe.get_doc("Business Document", archiving.archive(original.name))
		self.assertEqual(
			archiving.mail_meta(document)["rebuilt_at"],
			get_datetime(original.rebuilt_at).isoformat() + "Z",
		)

	def test_rebuild_window_passed(self):
		name = self.queued_send(make_communication(self.account.name, "Sent"))
		queue = frappe.db.get_value("Mail Original", name, "email_queue")
		frappe.db.set_value(
			"Email Queue Recipient",
			{"parent": queue},
			"modified",
			add_to_date(now_datetime(), hours=-2),
			update_modified=False,
		)
		original = self.stored(name)
		self.assertEqual((original.status, original.error), ("Failed", "rebuild window passed"))
		self.assertFalse(original.file)

	def test_queue_not_capturable_at_job_time_fails(self):
		name = self.queued_send(make_communication(self.account.name, "Sent"))
		queue = frappe.db.get_value("Mail Original", name, "email_queue")
		frappe.db.set_value("Email Queue", queue, "redact_message_after_send", 1)
		original = self.stored(name)
		self.assertEqual((original.status, original.attempts), ("Failed", 1))
		self.assertFalse(original.file)

	def capture(self):
		comm = make_communication(self.account.name)
		name = originals.capture(comm, self.raw, "test")
		return comm, name, frappe.db.get_value("Mail Original", name, "file")

	def test_deleting_communication_deletes_unarchived_original(self):
		comm, name, file_url = self.capture()
		frappe.delete_doc("Communication", comm.name, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("Mail Original", name))
		self.assertFalse(frappe.db.exists("File", {"file_url": file_url}))

	def test_deleting_communication_keeps_archived_original(self):
		comm, name, file_url = self.capture()
		document = make_business_document()
		frappe.db.set_value("Mail Original", name, "business_document", document.name)
		frappe.delete_doc("Communication", comm.name, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("Communication", comm.name))
		self.assertTrue(frappe.db.exists("File", {"file_url": document.file}))
		self.assertEqual(frappe.db.get_value("Business Document", document.name, "file"), document.file)
		original = frappe.db.get_value("Mail Original", name, ["business_document", "file"], as_dict=True)
		self.assertEqual((original.business_document, original.file), (document.name, file_url))
		self.assertTrue(frappe.db.exists("File", {"file_url": file_url}))
