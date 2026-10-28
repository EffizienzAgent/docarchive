from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import get_datetime

from docarchive.mail import archiving, inbox, states
from docarchive.tests.factories import eml, make_file, make_outsider, pdf, unique
from docarchive.tests.test_mail_archiving import MailArchivingTestCase

DOCUMENT = "Business Document"


class TestMailStates(MailArchivingTestCase):
	def test_states_cover_each_case(self):
		plain = self.communication()
		self.capture(eml(Subject="Kurze Frage"), plain)
		offer = self.communication()
		self.capture(eml(Subject="Angebot 2027"), offer)
		filed = self.communication()
		archiving.archive(self.capture(eml(Subject="Hallo"), filed))
		ignored = self.communication()
		inbox.ignore([self.capture(eml(Subject="Rechnung"), ignored)])
		bare = self.communication()
		got = archiving.statuses([plain.name, offer.name, filed.name, ignored.name, bare.name])
		self.assertEqual(
			[got[c.name]["state"] for c in (plain, offer, filed, ignored, bare)],
			["not_archived", "suggested", "filed", "ignored", "none"],
		)
		self.assertEqual(got[offer.name]["suggestion_reasons"], ["keyword:Angebot"])

	def test_statuses_accepts_json_and_dedupes(self):
		comm = self.communication()
		self.capture(eml(Subject="Hi"), comm)
		self.assertEqual(list(archiving.statuses(f'["{comm.name}", "{comm.name}"]')), [comm.name])

	def test_statuses_caps_names_after_dedupe(self):
		comm = self.communication()
		self.capture(eml(Subject="Hi"), comm)
		names = [comm.name] * 3 + [f"{comm.name}-{i}" for i in range(states.MAX_NAMES + 5)]
		with patch.object(states, "readable", wraps=states.readable) as readable:
			got = archiving.statuses(names)
		self.assertEqual(len(got), states.MAX_NAMES)
		self.assertEqual(len(readable.call_args.args[0]), states.MAX_NAMES)

	def test_unconfigured_or_off_account_is_none_but_keeps_can_archive(self):
		comm = self.communication()
		self.capture(eml(Subject="Rechnung"), comm)
		self.configure(mode="Off")
		off = archiving.status(comm.name)
		self.assertEqual((off["state"], off["suggestion_reasons"], off["can_archive"]), ("none", [], True))
		settings = frappe.get_single("Archive Settings")
		settings.set("mail_accounts", [])
		settings.save(ignore_permissions=True)
		self.assertEqual(archiving.status(comm.name)["state"], "none")

	def test_filed_mail_keeps_state_when_account_turns_off(self):
		comm = self.communication()
		archiving.archive(self.capture(eml(Subject="Hallo"), comm))
		self.configure(mode="Off")
		status = archiving.status(comm.name)
		self.assertEqual(status["state"], "filed")
		self.assertTrue(status["business_document"])

	def test_automated_internal_and_automatic_mode_are_not_suggested(self):
		automated = self.communication()
		self.capture(eml(Subject="Rechnung", **{"Auto-Submitted": "auto-generated"}), automated)
		internal = self.communication()
		self.capture(eml(Subject="Rechnung", From="me@acme.test"), internal)
		got = archiving.statuses([automated.name, internal.name])
		self.assertEqual(
			[got[automated.name]["state"], got[internal.name]["state"]], ["not_archived", "not_archived"]
		)
		comm = self.communication()
		self.capture(eml(Subject="Rechnung"), comm)
		self.configure(mode="Automatic")
		self.assertEqual(archiving.status(comm.name)["state"], "not_archived")

	def test_pdf_attachment_suggests(self):
		comm = self.communication()
		self.capture(eml(Subject="Hi"), comm)
		make_file(
			content=pdf(),
			file_name="scan.pdf",
			attached_to_doctype="Communication",
			attached_to_name=comm.name,
		)
		self.assertEqual(archiving.status(comm.name)["suggestion_reasons"], ["attachment:pdf"])

	def test_archived_state_and_status_compat(self):
		comm = self.communication()
		name = archiving.archive(self.capture(comm=comm))
		frappe.db.set_value(DOCUMENT, name, "archive_status", "Archived")
		self.assertEqual(archiving.status(comm.name), archiving.statuses([comm.name])[comm.name])
		status = archiving.status(comm.name)
		self.assertEqual((status["state"], status["archived"]), ("archived", True))

	def test_expires_on_from_keep_days(self):
		self.configure(keep_days=10)
		comm = self.communication()
		original = self.capture(eml(Subject="Hi"), comm)
		creation = frappe.db.get_value("Mail Original", original, "creation")
		self.assertEqual(archiving.status(comm.name)["expires_on"], (creation + timedelta(days=10)).date())

	def test_sealed_from_and_can_discard_for_filed(self):
		frappe.db.set_single_value("Archive Settings", {"enabled": 1, "min_age_hours": 24})
		frappe.get_cached_doc("Archive Settings").clear_cache()
		comm = self.communication()
		name = archiving.archive(self.capture(eml(Subject="Hi"), comm))
		frappe.db.set_value(DOCUMENT, name, "modified", "2026-10-28 03:00:00", update_modified=False)
		status = archiving.status(comm.name)
		self.assertEqual(status["sealed_from"], get_datetime("2026-10-30 02:30"))
		self.assertTrue(status["can_discard"])
		frappe.db.set_value(DOCUMENT, name, "archive_started_at", "2026-10-29 02:30:00")
		status = archiving.status(comm.name)
		self.assertEqual((status["sealed_from"], status["can_discard"]), (None, False))

	def test_next_run_after(self):
		self.assertEqual(
			states.next_run_after(get_datetime("2026-10-29 02:00")), get_datetime("2026-10-29 02:30")
		)
		self.assertEqual(
			states.next_run_after(get_datetime("2026-10-29 02:30")), get_datetime("2026-10-29 02:30")
		)
		self.assertEqual(
			states.next_run_after(get_datetime("2026-10-29 03:00")), get_datetime("2026-10-30 02:30")
		)

	def test_unreadable_communication_is_none_and_leaks_nothing(self):
		comm = self.communication()
		archiving.archive(self.capture(eml(Subject="Rechnung"), comm))
		self.act_as_outsider()
		status = archiving.status(comm.name)
		self.assertEqual(
			(status["state"], status["business_document"], status["suggestion_reasons"]), ("none", None, [])
		)
		for key in ("mail_original", "expires_on", "sealed_from"):
			self.assertIsNone(status[key])
		self.assertEqual((status["ignored"], status["can_discard"]), (False, False))

	def test_missing_document_row_is_filed_not_suggested(self):
		comm = self.communication()
		original = self.capture(eml(Subject="Rechnung"), comm)
		frappe.db.set_value("Mail Original", original, "business_document", None)
		frappe.db.sql(
			"update `tabMail Original` set business_document=%s where name=%s", ("missing-doc", original)
		)
		status = archiving.status(comm.name)
		self.assertEqual((status["archived"], status["state"], status["suggested"]), (True, "filed", False))

	def test_unreadable_document_is_filed_without_link(self):
		comm = self.communication()
		archiving.archive(self.capture(comm=comm))
		self.act_as_outsider(comm)
		status = archiving.status(comm.name)
		self.assertEqual(
			(status["state"], status["business_document"], status["archive_status"]), ("filed", None, None)
		)

	def test_reader_without_create_cannot_archive(self):
		comm = self.communication()
		self.capture(eml(Subject="Rechnung"), comm)
		self.act_as_outsider(comm)
		status = archiving.status(comm.name)
		self.assertEqual((status["can_archive"], status["state"]), (False, "suggested"))

	def test_query_count_is_constant(self):
		def count(size):
			comms = [self.communication() for _ in range(size)]
			for index, comm in enumerate(comms):
				original = self.capture(eml(Subject="Hi"), comm)
				if index % 2:
					archiving.archive(original)
			with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
				archiving.statuses([comm.name for comm in comms])
			return sql.call_count

		count(2)
		self.assertEqual(count(2), count(6))

	def act_as_outsider(self, shared=None):
		user = make_outsider(f"{unique('reader')}@example.test")
		if shared:
			frappe.share.add("Communication", shared.name, user, read=1)
		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")
