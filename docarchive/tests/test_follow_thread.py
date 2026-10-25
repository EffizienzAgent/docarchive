import json
from unittest.mock import patch

import frappe

from docarchive import audit, mail
from docarchive.mail import archiving, cleanup
from docarchive.tests.factories import eml, make_own_company, make_settings
from docarchive.tests.test_mail_archiving import MailArchivingTestCase
from docarchive.tests.test_mail_viewer import message_id, references

DOCUMENT = "Business Document"
ORIGINAL = "Mail Original"


class TestFollowThread(MailArchivingTestCase):
	def mail(self, at, sent=False, **headers):
		comm = self.communication("Sent" if sent else "Received", communication_date=at)
		if sent:
			headers = {"From": "info@acme.test", "To": "client@example.test", **headers}
		return comm, mail.capture(comm, eml(**headers), "t")

	def document(self, original):
		return frappe.db.get_value(ORIGINAL, original, "business_document")

	def reason(self, original):
		return frappe.db.get_value(DOCUMENT, self.document(original), "filing_reason")

	def conversation(self):
		z_id, a_id = message_id(), message_id()
		_, z = self.mail("2026-03-09 09:00:00", **{"Message-ID": f"<{z_id}>"})
		a_comm, a = self.mail(
			"2026-03-10 09:00:00",
			**{"Message-ID": f"<{a_id}>", "In-Reply-To": f"<{z_id}>", "References": references(z_id)},
		)
		archiving.archive_communications([a_comm.name])
		return a_id, z_id, a_comm, a, z

	def test_later_mails_of_an_archived_thread_are_archived(self):
		a_id, z_id, _a_comm, a, z = self.conversation()
		self.assertEqual(self.reason(a), "Manual")
		_, b = self.mail("2026-03-11 09:00:00", **{"In-Reply-To": f"<{a_id}>"})
		_, c = self.mail("2026-03-12 09:00:00", sent=True, References=references(z_id, a_id))
		self.assertEqual((self.reason(b), self.reason(c)), ("Thread", "Thread"))
		self.assertEqual(
			frappe.db.get_value(DOCUMENT, self.document(c), "correspondence_direction"), "Outgoing"
		)
		self.assertIsNone(self.document(z))

	def test_older_mails_are_listed_not_archived(self):
		_a_id, _z_id, a_comm, _a, z = self.conversation()
		result = mail.thread_pending(a_comm.name)
		self.assertEqual(result["count"], 1)
		[item] = result["items"]
		self.assertEqual((item["name"], item["subject"]), (z, "Offer"))
		self.assertEqual(set(item), {"name", "communication", "subject", "date"})

	def test_thread_pending_needs_the_mail_scope(self):
		_a_id, _z_id, a_comm, _a, _z = self.conversation()
		with patch("docarchive.mail.access.readable", return_value=set()):
			self.assertEqual(mail.thread_pending(a_comm.name), {"count": 0, "items": []})

	def test_other_own_company_is_not_followed(self):
		a_id, *_ = self.conversation()
		self.configure(own_company=make_own_company("acme-ltd").name)
		_, b = self.mail("2026-03-11 09:00:00", **{"In-Reply-To": f"<{a_id}>"})
		self.assertIsNone(self.document(b))

	def test_toggle_off(self):
		a_id, *_ = self.conversation()
		make_settings(default_own_company=self.company, follow_threads=0)
		self.configure()
		_, b = self.mail("2026-03-11 09:00:00", **{"In-Reply-To": f"<{a_id}>"})
		self.assertIsNone(self.document(b))

	def test_off_accounts_capture_nothing(self):
		a_id, *_ = self.conversation()
		self.configure(mode="Off")
		_, b = self.mail("2026-03-11 09:00:00", **{"In-Reply-To": f"<{a_id}>"})
		self.assertIsNone(b)

	def test_unrelated_mails_stay_unarchived(self):
		self.conversation()
		_, other = self.mail("2026-03-11 09:00:00")
		self.assertIsNone(self.document(other))

	def test_hook_and_daily_audit(self):
		a_id, *_ = self.conversation()
		with patch("docarchive.mail.events.changed") as changed:
			b_comm, b = self.mail("2026-03-11 09:00:00", **{"In-Reply-To": f"<{a_id}>"})
		changed.assert_called_once_with("archived", [b], [b_comm.name])
		before = set(frappe.get_all(audit.LOG, pluck="name"))
		cleanup.run()
		entries = [
			json.loads(row.body)
			for row in frappe.get_all(
				audit.LOG, filters={"action": audit.MAIL_ARCHIVED}, fields=["name", "body"]
			)
			if row.name not in before
		]
		[entry] = [entry for entry in entries if entry["details"]["how"] == "thread"]
		self.assertIn(self.document(b), [row["name"] for row in entry["documents"]])

	def test_automatic_mode_keeps_its_reason(self):
		self.configure(mode="Automatic")
		_, original = self.mail("2026-03-11 09:00:00")
		self.assertEqual(self.reason(original), "Automatic")
