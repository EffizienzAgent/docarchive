from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import get_datetime, now_datetime

from docarchive import mail
from docarchive.mail import archiving, cleanup, events, inbox
from docarchive.permissions import MANAGER_ROLE
from docarchive.tests.factories import eml, make_email_account, make_user
from docarchive.tests.test_mail_archiving import MailArchivingTestCase, address, make_contact, make_note

ORIGINAL = "Mail Original"
CHANGES = []


def record_change(**kwargs):
	CHANGES.append(kwargs)


def broken_hook(**kwargs):
	raise RuntimeError("host failed")


class CandidatesTestCase(MailArchivingTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.after_commit.reset()
		self.addCleanup(frappe.db.after_commit.reset)

	def age(self, original, days):
		frappe.db.set_value(
			ORIGINAL, original, "creation", now_datetime() - timedelta(days=days), update_modified=False
		)

	def names(self, **kwargs):
		return [row["name"] for row in mail.candidates(**kwargs)]

	def accounts(self, *rows):
		settings = frappe.get_single("Archive Settings")
		settings.set("mail_accounts", [])
		for account, mode, days in rows:
			settings.append(
				"mail_accounts",
				{"email_account": account, "mode": mode, "keep_days": days, "internal_domains": "acme.test"},
			)
		settings.save(ignore_permissions=True)

	def capture_in(self, account, raw=None):
		from docarchive.tests.factories import make_communication

		return mail.capture(make_communication(account), raw or eml(), "t")

	def as_user(self, *roles, accounts=()):
		user = make_user(address("reader"), roles)
		doc = frappe.get_doc("User", user)
		doc.set("user_emails", [{"email_account": account} for account in accounts])
		doc.save(ignore_permissions=True)
		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")
		return user


class TestCandidates(CandidatesTestCase):
	def test_item_fields(self):
		comm = self.communication()
		original = self.capture(eml(Subject="Hello", To="Info <info@acme.test>"), comm)
		(row,) = mail.candidates()
		stored = frappe.get_doc(ORIGINAL, original)
		self.assertEqual(
			{
				key: row[key]
				for key in ("name", "message_id", "subject", "from", "to", "account", "communication")
			},
			{
				"name": original,
				"message_id": stored.message_id,
				"subject": "Hello",
				"from": stored.sender,
				"to": stored.to,
				"account": self.account,
				"communication": comm.name,
			},
		)
		self.assertEqual(row["date"], stored.mail_at)
		self.assertEqual(row["expires_at"], get_datetime(stored.creation) + timedelta(days=14))
		self.assertEqual(
			(row["automated"], row["internal"], row["suggested_counterparty"]), (False, False, None)
		)

	def test_only_manual_accounts(self):
		self.capture()
		for mode in ("Off", "Automatic"):
			self.configure(mode=mode)
			self.assertEqual(mail.candidates(), [])
			self.assertEqual(mail.count_candidates(), 0)

	def test_only_stored_unarchived_and_not_ignored(self):
		kept = self.capture()
		archived = self.capture()
		archiving.archive(archived)
		ignored = self.capture()
		inbox.ignore([ignored])
		for status in ("Pending", "Failed"):
			frappe.db.set_value(ORIGINAL, self.capture(), "status", status)
		self.assertEqual(self.names(), [kept])

	def test_automated_and_internal_flagged(self):
		bulk = self.capture(eml(**{"List-Unsubscribe": "<mailto:off@example.test>"}))
		internal = self.capture(eml(From="a@acme.test", To="b@acme.test"))
		flags = {row["name"]: (row["automated"], row["internal"]) for row in mail.candidates()}
		self.assertEqual(flags, {bulk: (True, False), internal: (False, True)})

	def test_ordered_by_expiry_across_accounts(self):
		other = make_email_account().name
		self.accounts((self.account, "Manual", 30), (other, "Manual", 14))
		old = self.capture_in(self.account)
		self.age(old, 20)
		middle = self.capture_in(other)
		new = self.capture_in(self.account)
		self.assertEqual(self.names(), [old, middle, new])

	def test_expiring_within_days(self):
		soon = self.capture()
		self.age(soon, 12)
		self.capture()
		self.assertEqual(self.names(expiring_within_days=3), [soon])
		self.assertEqual(self.names(expiring_within_days="3"), [soon])
		self.assertEqual(mail.count_candidates(expiring_within_days=3), 1)
		self.assertEqual(mail.count_candidates(), 2)

	def test_limit(self):
		names = [self.capture() for _ in range(3)]
		self.assertEqual(self.names(limit=2), names[:2])
		with patch.object(inbox, "MAX_LIMIT", 2):
			self.assertEqual(len(mail.candidates(limit=1000)), 2)
		self.assertEqual(inbox.MAX_LIMIT, 200)

	def test_suggested_counterparty(self):
		party = make_note()
		email = address()
		make_contact(email, party)
		original = self.capture(eml(From=email), self.communication(sender=email))
		(row,) = mail.candidates()
		self.assertEqual(row["name"], original)
		self.assertEqual(
			row["suggested_counterparty"],
			{"counterparty_type": "Note", "counterparty": party.name, "title": party.title},
		)
		self.assertIsNone(frappe.db.get_value(ORIGINAL, original, "business_document"))

	def test_no_suggestion_for_two_parties(self):
		email = address()
		make_contact(email, make_note(), make_note())
		self.capture(eml(From=email), self.communication(sender=email))
		self.assertIsNone(mail.candidates()[0]["suggested_counterparty"])

	def test_no_create_permission_gives_nothing(self):
		self.capture()
		self.as_user("Inbox User", accounts=[self.account])
		self.assertEqual(mail.candidates(), [])
		self.assertEqual(mail.count_candidates(), 0)

	def test_unreadable_communication_excluded(self):
		other = make_email_account().name
		self.accounts((self.account, "Manual", 14), (other, "Manual", 14))
		readable = self.capture_in(self.account)
		self.capture_in(other)
		self.as_user(MANAGER_ROLE, "Inbox User", accounts=[self.account])
		self.assertEqual(self.names(), [readable])
		self.assertEqual(mail.count_candidates(), 1)

	def test_count_matches_candidates(self):
		other = make_email_account().name
		self.accounts((self.account, "Manual", 14), (other, "Manual", 30))
		for account in (self.account, other, other):
			self.capture_in(account)
		self.assertEqual(mail.count_candidates(), len(mail.candidates()))

	def test_query_count_does_not_grow_with_items(self):
		def make(count):
			for _ in range(count):
				email = address()
				make_contact(email, make_note())
				self.capture(eml(From=email), self.communication(sender=email))

		def queries():
			mail.candidates()
			with patch.object(frappe.db.__class__, "sql", autospec=True, side_effect=sql) as spy:
				rows = mail.candidates()
			return len(rows), spy.call_count

		sql = frappe.db.__class__.sql
		make(5)
		few = queries()
		make(45)
		many = queries()
		self.assertEqual((few[0], many[0]), (5, 50))
		self.assertLessEqual(many[1], few[1] + 2)


class TestIgnore(CandidatesTestCase):
	def test_ignore_and_unignore_in_bulk(self):
		first, second = self.capture(), self.capture()
		self.assertEqual(inbox.ignore(frappe.as_json([first, second])), {"ignored": [first, second]})
		row = frappe.db.get_value(ORIGINAL, first, ["ignored", "ignored_by", "ignored_at"], as_dict=True)
		self.assertEqual((row.ignored, row.ignored_by), (1, "Administrator"))
		self.assertTrue(row.ignored_at)
		self.assertEqual((mail.candidates(), mail.count_candidates()), ([], 0))
		self.assertEqual(inbox.ignore([first]), {"ignored": []})
		self.assertEqual(inbox.unignore([first]), {"unignored": [first]})
		row = frappe.db.get_value(ORIGINAL, first, ["ignored", "ignored_by", "ignored_at"], as_dict=True)
		self.assertEqual((row.ignored, row.ignored_by, row.ignored_at), (0, None, None))
		self.assertEqual(self.names(), [first])

	def test_ignored_original_still_expires(self):
		original = self.capture()
		inbox.ignore([original])
		self.age(original, 15)
		self.assertEqual(cleanup.run()["originals"], 1)
		self.assertFalse(frappe.db.exists(ORIGINAL, original))

	def test_ignored_original_can_be_archived(self):
		original = self.capture()
		inbox.ignore([original])
		result = mail.archive_originals([original])
		name = frappe.db.get_value(ORIGINAL, original, "business_document")
		self.assertEqual(result, {"archived": {original: name}, "refused": {}})

	def test_ignore_needs_create_permission(self):
		original = self.capture()
		self.as_user("Inbox User", accounts=[self.account])
		with self.assertRaises(frappe.PermissionError):
			inbox.ignore([original])

	def test_ignore_needs_readable_communication(self):
		other = make_email_account().name
		self.accounts((self.account, "Manual", 14), (other, "Manual", 14))
		original = self.capture_in(other)
		self.as_user(MANAGER_ROLE, "Inbox User", accounts=[self.account])
		for action in (inbox.ignore, inbox.unignore, mail.archive_originals):
			with self.assertRaises(frappe.PermissionError):
				action([original])
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.get_value(ORIGINAL, original, "ignored"))


class TestArchiveOriginals(CandidatesTestCase):
	def test_per_item_results(self):
		stored = self.capture()
		pending = self.capture()
		frappe.db.set_value(ORIGINAL, pending, "status", "Pending")
		result = mail.archive_originals(frappe.as_json([stored, pending]))
		self.assertEqual(
			result["archived"], {stored: frappe.db.get_value(ORIGINAL, stored, "business_document")}
		)
		self.assertEqual(list(result["refused"]), [pending])

	def test_archive_communications_accepts_a_list(self):
		comm = self.communication()
		self.capture(comm=comm)
		result = archiving.archive_communications([comm.name])
		self.assertEqual(list(result["archived"]), [comm.name])


class TestChangedHook(CandidatesTestCase):
	def setUp(self):
		super().setUp()
		CHANGES.clear()
		self.enterContext(
			patch.object(
				events, "methods", return_value=[f"{__name__}.broken_hook", f"{__name__}.record_change"]
			)
		)

	def changes(self):
		frappe.db.after_commit.run()
		return CHANGES

	def test_nothing_before_commit(self):
		inbox.ignore([self.capture()])
		self.assertEqual(CHANGES, [])

	def test_ignore_and_unignore(self):
		comm = self.communication()
		original = self.capture(comm=comm)
		with patch("frappe.log_error") as log_error:
			inbox.ignore([original])
			inbox.unignore([original])
			changes = self.changes()
		expected = {"originals": [original], "communications": [comm.name]}
		self.assertEqual(changes, [{"action": "ignored", **expected}, {"action": "unignored", **expected}])
		self.assertEqual(log_error.call_count, 2)

	def test_archive_paths(self):
		comms = [self.communication() for _ in range(3)]
		originals = [self.capture(comm=comm) for comm in comms]
		mail.archive_originals([originals[0]])
		archiving.archive_communications([comms[1].name])
		self.configure(mode="Automatic")
		auto = self.communication()
		auto_original = self.capture(comm=auto)
		with patch("frappe.log_error"):
			changes = self.changes()
		self.assertEqual(
			changes,
			[
				{"action": "archived", "originals": [originals[0]], "communications": [comms[0].name]},
				{"action": "archived", "originals": [originals[1]], "communications": [comms[1].name]},
				{"action": "archived", "originals": [auto_original], "communications": [auto.name]},
			],
		)

	def test_no_call_without_change(self):
		original = self.capture()
		inbox.unignore([original])
		self.assertEqual(self.changes(), [])

	def test_registered_hook_name(self):
		self.assertEqual(events.HOOK, "docarchive_mail_changed")
