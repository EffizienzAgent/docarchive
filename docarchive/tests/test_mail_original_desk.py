import base64
from unittest.mock import patch

import frappe

from docarchive.mail import archiving, inbox, viewer
from docarchive.tests.factories import eml, make_outsider
from docarchive.tests.test_mail_archiving import MailArchivingTestCase, address
from docarchive.tests.test_mail_viewer import mime

ORIGINAL = "Mail Original"


class TestMailOriginalViewer(MailArchivingTestCase):
	def test_render_an_unarchived_original(self):
		msg = mime(text="Hello there", Subject="Offer <b>now</b>")
		msg.add_attachment(b"%PDF-1.7 one", maintype="application", subtype="pdf", filename="offer.pdf")
		original = self.capture(msg.as_bytes())
		r = viewer.render(original, ORIGINAL)
		self.assertEqual(r["headers"]["subject"], "Offer <b>now</b>")
		self.assertEqual((r["sha256_ok"], r["origin"]), (True, "t"))
		self.assertIn("Hello there", r["text"])
		viewer.attachment(original, 0, ORIGINAL)
		self.assertEqual(frappe.response.filecontent, b"%PDF-1.7 one")
		self.assertFalse(frappe.db.get_value(ORIGINAL, original, "business_document"))

	def test_original_only_in_the_database_is_not_shown(self):
		original = self.capture()
		frappe.db.set_value(
			ORIGINAL,
			original,
			{"status": "Pending", "file": None, "raw_fallback": base64.b64encode(eml()).decode()},
		)
		with self.assertRaises(frappe.ValidationError) as caught:
			viewer.render(original, ORIGINAL)
		self.assertIn("still being stored", str(caught.exception))
		with self.assertRaises(frappe.ValidationError):
			viewer.attachment(original, 0, ORIGINAL)

	def test_original_needs_read_permission_and_the_mail_scope(self):
		original = self.capture()
		with patch("docarchive.mail.access.readable", return_value=set()):
			with self.assertRaises(frappe.PermissionError):
				viewer.render(original, ORIGINAL)
		frappe.set_user(make_outsider(address("original-outsider")))
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			viewer.render(original, ORIGINAL)
		with self.assertRaises(frappe.PermissionError):
			viewer.attachment(original, 0, ORIGINAL)

	def test_other_doctypes_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			viewer.render("x", "Communication")


class TestMailOriginalStatus(MailArchivingTestCase):
	def test_unarchived(self):
		original = self.capture()
		self.assertEqual(
			inbox.original_status(original),
			{
				"archived": False,
				"business_document": None,
				"ignored": False,
				"automated": False,
				"internal": False,
				"viewable": True,
				"reason": None,
			},
		)
		inbox.ignore([original])
		self.assertTrue(inbox.original_status(original)["ignored"])

	def test_archived(self):
		original = self.capture()
		name = archiving.archive(original)
		status = inbox.original_status(original)
		self.assertEqual((status["archived"], status["business_document"]), (True, name))

	def test_pending(self):
		original = self.capture()
		frappe.db.set_value(ORIGINAL, original, {"status": "Pending", "file": None})
		status = inbox.original_status(original)
		self.assertFalse(status["viewable"])
		self.assertIn("still being stored", status["reason"])

	def test_outside_the_scope(self):
		original = self.capture()
		with patch("docarchive.mail.inbox.readable", return_value=set()):
			status = inbox.original_status(original)
		self.assertEqual((status["viewable"], status["reason"]), (False, "You cannot read this e-mail."))

	def test_needs_read_permission(self):
		original = self.capture()
		frappe.set_user(make_outsider(address("status-outsider")))
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			inbox.original_status(original)

	def test_warnings_for_originals(self):
		bulk = self.capture(eml(Precedence="bulk"))
		internal = self.capture(eml(From="a@acme.test", To="b@acme.test"))
		plain = self.capture()
		archived = self.capture(eml(Precedence="bulk"))
		archiving.archive(archived)
		self.assertEqual(
			inbox.warnings([bulk, internal, plain, archived]), {"automated": [bulk], "internal": [internal]}
		)
