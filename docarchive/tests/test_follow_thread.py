import json
from unittest.mock import patch

import frappe

from docarchive import audit, mail
from docarchive.mail import archiving, cleanup
from docarchive.mail.archiving import schedule_follow_up as real_schedule_follow_up
from docarchive.tests.factories import eml, make_own_company, make_settings
from docarchive.tests.test_mail_archiving import MailArchivingTestCase
from docarchive.tests.test_mail_viewer import message_id, references

DOCUMENT = "Business Document"
ORIGINAL = "Mail Original"


class FollowThreadCase(MailArchivingTestCase):
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


class TestFollowThread(FollowThreadCase):
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


class TestFollowThreadRules(FollowThreadCase):
	def test_automated_and_internal_replies_follow_the_account_rules(self):
		a_id, *_ = self.conversation()
		_, bulk = self.mail(
			"2026-03-11 09:00:00",
			**{"In-Reply-To": f"<{a_id}>", "List-Unsubscribe": "<mailto:x@example.test>"},
		)
		_, internal = self.mail(
			"2026-03-11 10:00:00", From="a@acme.test", To="b@acme.test", **{"In-Reply-To": f"<{a_id}>"}
		)
		self.assertEqual((self.document(bulk), self.document(internal)), (None, None))
		self.configure(archive_internal=1)
		_, internal = self.mail(
			"2026-03-11 11:00:00", From="a@acme.test", To="b@acme.test", **{"In-Reply-To": f"<{a_id}>"}
		)
		self.assertEqual(self.reason(internal), "Thread")

	def test_manual_archive_follows_later_stored_mails(self):
		a_id, b_id = message_id(), message_id()
		a_comm, _a = self.mail("2026-03-10 09:00:00", **{"Message-ID": f"<{a_id}>"})
		_, b = self.mail("2026-03-11 09:00:00", **{"Message-ID": f"<{b_id}>", "In-Reply-To": f"<{a_id}>"})
		_, ignored = self.mail("2026-03-12 09:00:00", **{"In-Reply-To": f"<{a_id}>"})
		mail.ignore([ignored])
		self.assertEqual({item["name"] for item in mail.thread_pending(a_comm.name)["items"]}, {b})
		archiving.archive_communications([a_comm.name])
		self.assertEqual(self.reason(b), "Thread")
		self.assertIsNone(self.document(ignored))

	def test_counterparty_only_for_the_same_party(self):
		from docarchive.tests.factories import make_party, party_fields

		a_id, _z_id, _a_comm, a, _z = self.conversation()
		frappe.db.set_value(DOCUMENT, self.document(a), party_fields(make_party()))
		_, same = self.mail("2026-03-11 09:00:00", **{"In-Reply-To": f"<{a_id}>"})
		_, other = self.mail(
			"2026-03-11 10:00:00", From="Other <other@example.test>", **{"In-Reply-To": f"<{a_id}>"}
		)
		party = frappe.db.get_value(DOCUMENT, self.document(a), "counterparty")
		self.assertEqual(frappe.db.get_value(DOCUMENT, self.document(same), "counterparty"), party)
		self.assertIsNone(frappe.db.get_value(DOCUMENT, self.document(other), "counterparty"))

	def test_counterparty_from_the_earliest_document_of_the_same_party(self):
		from docarchive.tests.factories import make_party, party_fields

		a_id, _z_id, _a_comm, a, z = self.conversation()
		archiving.archive(z)
		frappe.db.set_value(
			DOCUMENT,
			self.document(z),
			{**party_fields(make_party("Other")), "source_party_email": "other@example.test"},
		)
		mine = make_party("Mine")
		frappe.db.set_value(DOCUMENT, self.document(a), party_fields(mine))
		_, reply = self.mail("2026-03-11 09:00:00", **{"In-Reply-To": f"<{a_id}>"})
		self.assertEqual(frappe.db.get_value(DOCUMENT, self.document(reply), "counterparty"), mine.name)

	def test_same_message_id_always_links(self):
		a_id, *_ = self.conversation()
		_, copy = self.mail("2026-03-01 09:00:00", **{"Message-ID": f"<{a_id}>"})
		self.assertEqual(
			self.document(copy), frappe.db.get_value(DOCUMENT, {"mail_message_id": a_id}, "name")
		)

	def test_documents_without_a_date_do_not_count(self):
		a_id, _z_id, _a_comm, a, _z = self.conversation()
		frappe.db.set_value(DOCUMENT, self.document(a), "correspondence_at", None)
		_, b = self.mail("2026-03-11 09:00:00", **{"In-Reply-To": f"<{a_id}>"})
		self.assertIsNone(self.document(b))


class TestFollowUpJob(FollowThreadCase):
	def test_follow_up_runs_in_its_own_job_after_commit(self):
		with patch("frappe.enqueue") as enqueue:
			real_schedule_follow_up(["DOC-1", "DOC-1"])
		enqueue.assert_called_once_with(
			archiving.FOLLOW_JOB,
			queue="default",
			job_id="docarchive-follow-thread-DOC-1",
			deduplicate=True,
			enqueue_after_commit=True,
			document="DOC-1",
		)

	def test_follow_up_errors_never_reach_the_user(self):
		messages = len(frappe.local.message_log)

		def broken(document):
			frappe.msgprint("noise")
			raise frappe.ValidationError("boom")

		with (
			patch("docarchive.mail.thread.later_originals", side_effect=broken),
			patch("frappe.log_error") as log_error,
		):
			self.assertEqual(archiving.follow_up(["DOC-1"]), 0)
		log_error.assert_called_once()
		self.assertEqual(len(frappe.local.message_log), messages)
