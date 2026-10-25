from unittest.mock import patch

import frappe
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.email.receive import InboundMail, SentEmailInInboxError
from frappe.tests import IntegrationTestCase

from docarchive import content
from docarchive.mail import originals
from docarchive.tests.factories import (
	eml,
	make_communication,
	make_email_account,
	make_mail_settings,
	make_settings,
	unique,
)


class TestMailHooks(IntegrationTestCase):
	def setUp(self):
		make_settings()
		self.email_id = f"{unique('info')}@acme.test"
		self.account = make_email_account(
			self.email_id, enable_outgoing=1, smtp_server="smtp.example.test", smtp_port=587
		)
		make_mail_settings(self.account.name, mode="Manual")
		self.message_id = f"{unique('mid')}@example.test"
		self.raw = eml(To=self.email_id, **{"Message-ID": f"<{self.message_id}>"})
		commit = patch.object(frappe.db, "commit")
		commit.start()
		self.addCleanup(commit.stop)

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

	def test_receive_succeeds_when_capture_fails(self):
		with patch("docarchive.mail.originals.store_file", side_effect=Exception("boom")):
			comm = self.receive().process()
		self.assertEqual(comm.name, self.received())
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

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

	def test_send_succeeds_when_capture_fails(self):
		comm = make_communication(self.account.name, "Sent")
		with patch("docarchive.mail.originals.store_file", side_effect=Exception("boom")):
			self.send(comm)
		self.assertTrue(frappe.flags.sent_mail)
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def test_redacted_mail_not_captured(self):
		comm = make_communication(self.account.name, "Sent")
		self.send(comm, redact_message_after_send=1)
		self.assertTrue(frappe.flags.sent_mail)
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def queue_for(self, comm):
		return frappe.get_doc("Email Queue", {"communication": comm.name})

	def test_newsletter_not_captured(self):
		comm = make_communication(self.account.name, "Sent")
		self.send(comm)
		frappe.delete_doc("Mail Original", frappe.db.get_value("Mail Original", {"communication": comm.name}))
		queue = self.queue_for(comm)
		queue.reference_doctype = "Newsletter"
		self.assertIsNone(originals.capture_sent(queue))
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def test_unsent_queue_not_captured(self):
		comm = make_communication(self.account.name, "Sent")
		with patch.dict(frappe.flags, {"mute_emails": True}):
			self.send(comm)
		self.assertIsNone(originals.capture_sent(self.queue_for(comm)))
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

	def test_capture_sent_never_raises(self):
		with patch("docarchive.mail.originals.SendMailContext", side_effect=Exception("boom")):
			comm = make_communication(self.account.name, "Sent")
			self.send(comm)
		self.assertTrue(frappe.flags.sent_mail)
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": comm.name}))

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
		frappe.db.set_value("Mail Original", name, "business_document", unique("BD"))
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("Communication", comm.name, ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Mail Original", name))
		self.assertTrue(frappe.db.exists("File", {"file_url": file_url}))
