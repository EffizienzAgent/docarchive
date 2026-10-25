from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

from docarchive import archive_now, monitoring, notify, run, store
from docarchive.permissions import ADMIN_ROLE
from docarchive.tests.factories import fields_target, make_document_type, make_settings, make_user
from docarchive.tests.test_run import DOCUMENT, ArchiveRunTestCase

ADMIN = "monitoring-admin@example.test"


class MonitoringTestCase(ArchiveRunTestCase):
	def setUp(self):
		super().setUp()
		make_user(ADMIN, [ADMIN_ROLE])
		frappe.db.delete("Notification Log")
		for doctype in ("Archive Audit Log", "Archive Audit Document", "Archive Audit Copy"):
			frappe.db.delete(doctype)
		frappe.cache.delete_keys("docarchive:alerted:")
		frappe.db.set_single_value("Archive Settings", "last_run_finished_at", None)
		self.enterContext(patch("frappe.enqueue"))

	def alerts(self, condition=None):
		filters = {"for_user": ADMIN}
		if condition:
			filters["link"] = ("like", f"%?alert={condition}%")
		return frappe.get_all(
			"Notification Log", filters=filters, fields=["subject", "email_content", "link"]
		)

	def broken(self, **fields):
		doc = self.document(**fields)
		frappe.db.set_value(DOCUMENT, doc.name, "content_sha256", "0" * 64, update_modified=False)
		return doc


class TestAlerts(MonitoringTestCase):
	def test_failed_documents_alert_once_a_day_each(self):
		first = self.broken()
		run.nightly()
		[alert] = self.alerts("failed")
		self.assertIn(first.name, alert.email_content)
		run.nightly()
		self.assertEqual(len(self.alerts("failed")), 1)
		second = self.broken()
		run.nightly()
		alerts = self.alerts("failed")
		self.assertEqual(len(alerts), 2)
		newest = next(a for a in alerts if second.name in a.email_content)
		self.assertNotIn(first.name, newest.email_content)

	def test_archive_now_failure_alerts(self):
		doc = self.broken()
		archive_now.run_job([doc.name])
		[alert] = self.alerts("failed")
		self.assertIn(doc.name, alert.email_content)

	def test_successful_run_raises_no_alert_and_records_the_time(self):
		self.document()
		run.nightly()
		self.assertEqual(self.alerts(), [])
		self.assertTrue(frappe.db.get_single_value("Archive Settings", "last_run_finished_at"))

	def test_failing_run_alerts_and_raises(self):
		self.document()
		with (
			patch("docarchive.run.reassign", side_effect=RuntimeError("db down")),
			patch("frappe.db.rollback"),
			self.assertRaises(RuntimeError),
		):
			run.nightly()
		[alert] = self.alerts("run_failed")
		self.assertIn("RuntimeError", alert.email_content)

	def test_documents_left_behind(self):
		busy, done = self.document(), self.document()
		real = run.document_lock

		def lock(name):
			result = real(name)
			if name == busy.name:
				result.acquire = lambda blocking=False: False
			return result

		with patch("docarchive.run.document_lock", side_effect=lock):
			run.nightly()
		[alert] = self.alerts("left_behind")
		self.assertIn(busy.name, alert.email_content)
		self.assertNotIn(done.name, alert.email_content)

	def test_overdue_documents_and_a_missed_run(self):
		late = self.document(age_hours=72)
		self.document(age_hours=30)
		monitoring.daily()
		[overdue] = self.alerts("overdue")
		self.assertIn(late.name, overdue.email_content)
		self.assertEqual(overdue.email_content.count("<br>"), 0)
		self.assertEqual(len(self.alerts("run_missed")), 1)
		monitoring.daily()
		self.assertEqual(len(self.alerts()), 2)

	def test_recent_run_is_not_missed(self):
		frappe.db.set_single_value("Archive Settings", "last_run_finished_at", now_datetime())
		monitoring.daily()
		self.assertEqual(self.alerts("run_missed"), [])

	def test_nothing_is_checked_while_archiving_is_off(self):
		self.configure(["primary"], enabled=0)
		self.document(age_hours=72)
		monitoring.daily()
		self.assertEqual(self.alerts(), [])

	def test_missing_copies(self):
		self.configure(["primary"])
		doc = self.document()
		self.assertEqual(run.archive_document(doc.name, store.targets()), "Archived")
		self.configure(["primary", "secondary"])
		monitoring.check()
		[alert] = self.alerts("missing_copies")
		self.assertIn(doc.name, alert.email_content)

	def test_long_lists_are_capped(self):
		with patch("docarchive.run.missing_copies", return_value=[f"DOC-{i}" for i in range(80)]):
			monitoring.check()
		[alert] = self.alerts("missing_copies")
		self.assertIn("30", alert.email_content.rsplit("<br>", 1)[-1])
		self.assertEqual(alert.email_content.count("<br>"), notify.ALERT_LINES)

	def test_failed_extension_alerts(self):
		monitoring.after_run({"failed": [], "extension_failed": ["DOC-X"], "skipped": []})
		[alert] = self.alerts("extension_failed")
		self.assertIn("DOC-X", alert.email_content)

	def test_monitoring_error_is_not_a_failed_run(self):
		self.document()
		with patch("docarchive.monitoring.check", side_effect=RuntimeError("x")), patch("frappe.db.rollback"):
			self.assertEqual(run.nightly()["status"], "done")
		self.assertEqual(self.alerts("run_failed"), [])
		self.assertTrue(frappe.db.get_single_value("Archive Settings", "last_run_finished_at"))

	def test_enabled_without_targets_says_so_once(self):
		with patch("docarchive.store.targets", return_value=None):
			monitoring.daily()
			monitoring.daily()
		self.assertEqual(len(self.alerts("not_configured")), 1)
		self.assertEqual(self.alerts("run_missed") + self.alerts("overdue"), [])

	def test_alert_link_filters_the_report(self):
		self.broken()
		run.nightly()
		[alert] = self.alerts("failed")
		self.assertTrue(alert.link.endswith("&problem=Failed"))

	def test_falls_back_to_system_managers(self):
		frappe.db.delete("Has Role", {"role": ADMIN_ROLE, "parenttype": "User"})
		manager = make_user("monitoring-sm@example.test", ["System Manager"])
		self.assertIn(manager, frappe.get_all("Has Role", {"role": "System Manager"}, pluck="parent"))
		monitoring.run_failed("x")
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": manager}))


class TestReport(MonitoringTestCase):
	def test_lists_overdue_failed_and_missing_copies(self):
		self.configure(["primary"])
		late = self.document(age_hours=72)
		failed = self.broken()
		copied = self.document()
		run.archive_document(copied.name, store.targets())
		run.archive_document(failed.name, store.targets())
		make_settings(
			enabled=1,
			default_own_company=self.company,
			targets=[fields_target("primary"), fields_target("secondary")],
		)
		frappe.db.set_value(
			DOCUMENT, late.name, "modified", add_to_date(now_datetime(), hours=-72), update_modified=False
		)
		rows = {row["name"]: row for row in monitoring.problems()}
		self.assertEqual(rows[failed.name]["problem"], monitoring.FAILED)
		self.assertEqual(
			(rows[copied.name]["problem"], rows[copied.name]["details"]),
			(monitoring.MISSING_COPIES, "secondary"),
		)
		self.assertEqual(rows[late.name]["problem"], monitoring.OVERDUE)
		only = monitoring.problems({"problem": monitoring.FAILED})
		self.assertEqual([row["name"] for row in only], [failed.name])
		self.assertEqual(monitoring.problem_count_card()["value"], len(rows))

	def test_number_gaps_per_type_company_and_prefix(self):
		numbered = make_document_type(number_required=1)
		for number in ("RE-2026-001", "RE-2026-004", "RE-2026-005", "RE-2026-007", "GS-10", "GS-11"):
			self.document(document_type=numbered.name, document_number=number, age_hours=1)
		rows = monitoring.problems({"number_gaps": 1, "document_type": numbered.name})
		gaps = [
			(row["document_number"], row["details"])
			for row in rows
			if row["problem"] == monitoring.NUMBER_GAP
		]
		self.assertEqual(gaps, [("RE-2026-002 - RE-2026-003", "2 missing"), ("RE-2026-006", "1 missing")])
		self.assertNotIn(monitoring.NUMBER_GAP, {row["problem"] for row in monitoring.problems()})

	def test_unpadded_numbers_across_a_power_of_ten(self):
		numbered = make_document_type(number_required=1)
		for number in ("GS-8", "GS-11"):
			self.document(document_type=numbered.name, document_number=number, age_hours=1)
		rows = monitoring.problems({"number_gaps": 1, "document_type": numbered.name})
		self.assertEqual([row["document_number"] for row in rows], ["GS-9 - GS-10"])

	def test_padded_and_unpadded_series_stay_apart(self):
		numbered = make_document_type(number_required=1)
		for number in ("R-01", "R-02", "R-10", "R-5"):
			self.document(document_type=numbered.name, document_number=number, age_hours=1)
		rows = monitoring.problems({"number_gaps": 1, "document_type": numbered.name})
		self.assertEqual([row["document_number"] for row in rows], ["R-03 - R-09"])

	def test_failed_without_error_text_and_since_archive_start(self):
		doc = self.document()
		started = add_to_date(now_datetime(), days=-14)
		frappe.db.set_value(
			DOCUMENT,
			doc.name,
			{"archive_status": "Failed", "archive_error": None, "archive_started_at": started},
		)
		[row] = [row for row in monitoring.problems() if row["name"] == doc.name]
		self.assertEqual((row["problem"], row["details"], row["since"]), (monitoring.FAILED, None, started))
