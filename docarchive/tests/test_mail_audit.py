import json

import frappe

from docarchive import audit, mail
from docarchive.mail import archiving, inbox
from docarchive.tests.factories import eml
from docarchive.tests.test_mail_archiving import MailArchivingTestCase
from docarchive.tests.test_mail_upload import upload

DOCUMENT = "Business Document"
ORIGINAL = "Mail Original"


class TestMailAudit(MailArchivingTestCase):
	def setUp(self):
		super().setUp()
		self.since = frappe.get_all(audit.LOG, pluck="name")

	def entries(self, action):
		rows = frappe.get_all(
			audit.LOG,
			filters={"action": action, "name": ("not in", self.since or [""])},
			fields=["name", "body"],
			order_by="at asc",
		)
		result = []
		for row in rows:
			body = json.loads(row.body)
			documents = [(d["doctype"], d["name"], d["outcome"]) for d in body["documents"]]
			result.append((body["details"], documents))
		return result

	def document_of(self, original):
		return frappe.db.get_value(ORIGINAL, original, "business_document")

	def test_manual_archive_logs_created_documents(self):
		comms = [self.communication() for _ in range(2)]
		originals = [self.capture(comm=comm) for comm in comms]
		missing = self.communication()
		archiving.archive_communications([comm.name for comm in comms] + [missing.name])
		self.assertEqual(
			self.entries(audit.MAIL_ARCHIVED),
			[
				(
					{"how": "manual", "counts": {"created": 2}, "refused": 1},
					[(DOCUMENT, self.document_of(original), "created") for original in originals],
				)
			],
		)

	def test_existing_document_is_linked(self):
		raw = eml()
		first = self.capture(raw)
		archiving.archive(first)
		second = self.capture(raw)
		mail.archive_originals([second])
		(entry,) = self.entries(audit.MAIL_ARCHIVED)
		self.assertEqual(entry[1], [(DOCUMENT, self.document_of(first), "linked")])

	def test_already_archived_logs_nothing(self):
		original = self.capture()
		archiving.archive(original)
		mail.archive_originals([original])
		self.assertEqual(self.entries(audit.MAIL_ARCHIVED), [])

	def test_automatic_archive(self):
		self.configure(mode="Automatic")
		original = self.capture()
		self.assertEqual(
			self.entries(audit.MAIL_ARCHIVED),
			[({"how": "automatic"}, [(DOCUMENT, self.document_of(original), "created")])],
		)

	def test_discard(self):
		name = archiving.archive(self.capture())
		held = archiving.archive(self.capture())
		frappe.db.set_value(DOCUMENT, held, "hold_on_archive", 1)
		archiving.discard([name, held])
		self.assertEqual(
			self.entries(audit.MAIL_DISCARDED), [({"refused": 1}, [(DOCUMENT, name, "discarded")])]
		)

	def test_ignore_and_unignore_log_originals(self):
		original = self.capture()
		inbox.ignore([original])
		inbox.ignore([original])
		inbox.unignore([original])
		self.assertEqual(self.entries(audit.MAIL_IGNORED), [({}, [(ORIGINAL, original, "ignored")])])
		self.assertEqual(self.entries(audit.MAIL_UNIGNORED), [({}, [(ORIGINAL, original, "unignored")])])

	def test_upload(self):
		name = archiving.archive_upload(upload(eml()).file_url, "Incoming")
		self.assertEqual(
			self.entries(audit.MAIL_UPLOADED),
			[({"direction": "Incoming", "gap": False}, [(DOCUMENT, name, "created")])],
		)

	def test_no_personal_data_in_entries(self):
		comm = self.communication()
		self.capture(eml(Subject="Secret offer", From="Jane <jane@example.test>"), comm)
		archiving.archive_communications([comm.name])
		bodies = frappe.get_all(audit.LOG, filters={"name": ("not in", self.since or [""])}, pluck="body")
		self.assertTrue(bodies)
		for body in bodies:
			self.assertNotIn("Secret", body)
			self.assertNotIn("example.test", body)
