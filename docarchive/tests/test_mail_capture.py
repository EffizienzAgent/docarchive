import hashlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import content, mail
from docarchive.mail import headers
from docarchive.tests.factories import (
	EML,
	eml,
	make_communication,
	make_email_account,
	make_mail_settings,
	make_settings,
)


class TestMailCapture(IntegrationTestCase):
	def setUp(self):
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
		with patch("docarchive.mail.originals.store_file", side_effect=Exception("boom")):
			self.assertIsNone(mail.capture(self.comm, EML, "t"))
		self.assertFalse(frappe.db.exists("Mail Original", {"communication": self.comm.name}))

	def test_auto_archive_called_after_capture(self):
		with patch("docarchive.mail.originals.archiving.auto_archive") as auto:
			name = mail.capture(self.comm, EML, "t")
		auto.assert_called_once_with(name)
