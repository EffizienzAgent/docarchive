import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from docarchive import extension, run, store
from docarchive.tests.factories import make_manager, make_outsider
from docarchive.tests.test_archive_now import run_now
from docarchive.tests.test_run import DOCUMENT, ArchiveRunTestCase

INITIAL = "2035-01-02 00:00:00"


class TestManualExtension(ArchiveRunTestCase):
	def setUp(self):
		super().setUp()
		self.enqueue = self.enterContext(patch("frappe.enqueue", side_effect=run_now))
		self.enterContext(patch("frappe.publish_realtime"))
		self.addCleanup(frappe.set_user, "Administrator")

	def archived(self):
		doc = self.document()
		self.assertEqual(run.archive_document(doc.name, store.targets()), run.ARCHIVED)
		doc.reload()
		self.assertEqual(str(doc.retain_until), INITIAL)
		return doc

	def retention_calls(self):
		return [k for fake in self.fakes.values() for op, k in fake.calls if op == "put_object_retention"]

	def copies(self, doc):
		doc.reload()
		return [str(copy.retain_until) for copy in doc.archive_copies]

	def test_adds_years_and_records_who_and_why(self):
		doc = self.archived()
		result = extension.extend(doc.name, extension.ADD_YEARS, years=2, reason="Audit 2026")
		self.assertEqual(result, {"done": True, "retain_until": "2037-01-02T00:00:00Z"})
		self.assertEqual(self.copies(doc), ["2037-01-02 00:00:00"] * 2)
		self.assertEqual(str(doc.retain_until), "2037-01-02 00:00:00")
		[entry] = json.loads(doc.retention_history)
		self.assertEqual(
			{k: entry[k] for k in ("kind", "extended_by", "reason", "previous_retain_until", "retain_until")},
			{
				"kind": "manual",
				"extended_by": "Administrator",
				"reason": "Audit 2026",
				"previous_retain_until": "2035-01-02T00:00:00Z",
				"retain_until": "2037-01-02T00:00:00Z",
			},
		)
		[put] = [p for p in self.fake("primary").puts() if p["Key"] == doc.object_key + ".meta.2.json"]
		self.assertEqual(json.loads(put["Body"])["extension"]["reason"], "Audit 2026")
		self.assertEqual(put["ObjectLockRetainUntilDate"], datetime(2037, 1, 2, tzinfo=UTC))

	def test_at_least_counts_from_the_retention_start(self):
		doc = self.archived()
		extension.extend(doc.name, extension.AT_LEAST, years=10)
		self.assertEqual(self.copies(doc), ["2037-01-02 00:00:00"] * 2)

	def test_to_date(self):
		doc = self.archived()
		extension.extend(doc.name, extension.TO_DATE, until="2040-06-30")
		self.assertEqual(self.copies(doc), ["2040-07-01 00:00:00"] * 2)

	def test_never_shortens_through_any_entry_point(self):
		doc = self.archived()
		attempts = {
			"earlier date": dict(how=extension.TO_DATE, until="2034-12-31"),
			"same date": dict(how=extension.TO_DATE, until="2035-01-01"),
			"fewer years": dict(how=extension.AT_LEAST, years=5),
			"zero years": dict(how=extension.ADD_YEARS, years=0),
			"negative years": dict(how=extension.ADD_YEARS, years=-3),
		}
		for case, args in attempts.items():
			with self.subTest(case):
				with self.assertRaises(frappe.ValidationError):
					extension.extend(doc.name, **args)
				if "years" not in args or args["years"] > 0:
					[row] = extension.preview(names=[doc.name], **args)["rows"]
					self.assertTrue(row["skipped"])
					extension.extend_many(names=[doc.name], **args)
		with self.assertRaises(frappe.ValidationError):
			extension.apply(doc, datetime(2030, 1, 2, tzinfo=UTC))
		run.nightly()
		self.assertEqual(self.retention_calls(), [])
		self.assertEqual(self.copies(doc), [INITIAL] * 2)
		self.assertEqual(doc.retention_history, None)

	def test_bulk_preview_and_job(self):
		short, long = self.archived(), self.archived()
		extension.extend(long.name, extension.TO_DATE, until="2045-01-01")
		preview = extension.preview(
			how=extension.AT_LEAST, years=10, counterparty_type="Contact", counterparty=self.party.name
		)
		rows = {row["name"]: row for row in preview["rows"]}
		self.assertEqual(preview["count"], 1)
		self.assertEqual(rows[short.name]["new"], "2037-01-02T00:00:00Z")
		self.assertTrue(rows[long.name]["skipped"])
		result = extension.run_many([short.name, long.name], extension.AT_LEAST, years=10)
		self.assertEqual(result["extended"], [short.name])
		self.assertEqual(list(result["skipped"]), [long.name])
		self.assertEqual(self.copies(short), ["2037-01-02 00:00:00"] * 2)
		self.assertEqual(self.copies(long), ["2045-01-02 00:00:00"] * 2)

	def test_failed_target_is_finished_by_the_nightly_run(self):
		doc = self.archived()
		self.fake("secondary").fail.add("put_object_retention")
		self.assertFalse(extension.extend(doc.name, extension.ADD_YEARS, years=1)["done"])
		doc.reload()
		self.assertEqual(str(doc.extension_until), "2036-01-02 00:00:00")
		self.assertEqual(str(doc.retain_until), INITIAL)
		with self.assertRaisesRegex(frappe.ValidationError, "still running"):
			extension.extend(doc.name, extension.ADD_YEARS, years=1)
		self.fake("secondary").fail.clear()
		run.nightly()
		self.assertEqual(self.copies(doc), ["2036-01-02 00:00:00"] * 2)
		self.assertEqual((str(doc.retain_until), doc.extension_until), ("2036-01-02 00:00:00", None))

	def test_only_archived_documents(self):
		doc = self.document()
		with self.assertRaises(frappe.ValidationError):
			extension.extend(doc.name, extension.ADD_YEARS, years=1)
		self.assertFalse(extension.options(doc.name)["allowed"])

	def test_options_show_copies_for_managers(self):
		doc = self.archived()
		options = extension.options(doc.name)
		self.assertTrue(options["allowed"])
		self.assertEqual(
			[(c["target"], c["mode"], c["retain_until"]) for c in options["copies"]],
			[
				("primary", "GOVERNANCE", "2035-01-02T00:00:00Z"),
				("secondary", "GOVERNANCE", "2035-01-02T00:00:00Z"),
			],
		)

	def test_non_managers_are_refused(self):
		doc = self.archived()
		user = make_outsider("extend-outsider@example.com")
		frappe.share.add(DOCUMENT, doc.name, user, read=1, write=1)
		with self.set_user(user):
			self.assertFalse(extension.options(doc.name)["allowed"])
			for call in (
				lambda: extension.extend(doc.name, extension.ADD_YEARS, years=1),
				lambda: extension.preview(how=extension.ADD_YEARS, years=1, names=[doc.name]),
				lambda: extension.extend_many(how=extension.ADD_YEARS, years=1, names=[doc.name]),
			):
				with self.assertRaises(frappe.PermissionError):
					call()

	def test_managers_can_extend(self):
		doc = self.archived()
		with self.set_user(make_manager("extend-manager@example.com")):
			self.assertTrue(extension.extend(doc.name, extension.ADD_YEARS, years=1)["done"])

	def test_mutating_endpoints_are_post_only(self):
		for fn in (extension.extend, extension.extend_many, extension.preview, extension.options):
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[fn], ["POST"])

	def test_count_ending(self):
		doc = self.archived()
		self.assertEqual(extension.count_ending(90), 0)
		soon = now_datetime() + timedelta(days=30)
		frappe.db.set_value(DOCUMENT, doc.name, "retain_until", soon, update_modified=False)
		self.assertEqual(extension.count_ending(90), 1)
		self.assertEqual(extension.ending_card(), {"value": 1, "fieldtype": "Int"})

	def test_dates_too_far_ahead_are_refused(self):
		doc = self.archived()
		for args in (
			dict(how=extension.TO_DATE, until="2200-01-01"),
			dict(how=extension.ADD_YEARS, years=99),
		):
			with self.subTest(args), self.assertRaisesRegex(frappe.ValidationError, "100"):
				extension.extend(doc.name, **args)
		self.assertEqual(self.retention_calls(), [])

	def test_missing_target_refuses_the_manual_extension(self):
		doc = self.archived()
		self.configure(["primary"])
		with self.assertRaisesRegex(frappe.ValidationError, "secondary"):
			extension.extend(doc.name, extension.ADD_YEARS, years=1)
		self.assertEqual(self.retention_calls(), [])
		self.assertEqual(self.copies(doc), [INITIAL] * 2)

	def test_document_date_is_never_lowered(self):
		doc = self.archived()
		frappe.db.set_value(DOCUMENT, doc.name, "retain_until", "2040-01-02 00:00:00", update_modified=False)
		doc.reload()
		until = datetime(2037, 1, 2, tzinfo=UTC)
		run.prepare_extension(doc, until)
		self.assertTrue(run.extend_document(doc, until, {t.name: t for t in store.targets()}))
		doc.reload()
		self.assertEqual(str(doc.retain_until), "2040-01-02 00:00:00")

	def test_bulk_job_id_depends_on_the_options(self):
		doc = self.archived()
		self.enqueue.side_effect = None
		extension.extend_many(names=[doc.name], how=extension.AT_LEAST, years=10)
		extension.extend_many(names=[doc.name], how=extension.AT_LEAST, years=12)
		first, second = (call.kwargs["job_id"] for call in self.enqueue.call_args_list)
		self.assertNotEqual(first, second)

	def test_state_is_checked_again_under_the_lock(self):
		doc = self.archived()
		frappe.db.set_value(
			DOCUMENT, doc.name, "extension_until", "2036-01-02 00:00:00", update_modified=False
		)
		with self.assertRaisesRegex(frappe.ValidationError, "still running"):
			extension.apply(doc, datetime(2037, 1, 2, tzinfo=UTC))
		self.assertEqual(self.retention_calls(), [])

	def test_deleted_documents_are_never_extended(self):
		by_copy, by_status = self.archived(), self.archived()
		frappe.db.set_value("Archive Copy", by_copy.archive_copies[0].name, "status", "Deleted")
		frappe.db.set_value(DOCUMENT, by_status.name, "archive_status", "Deleted", update_modified=False)
		before = len(self.retention_calls())
		for doc in (by_copy, by_status):
			with self.subTest(doc.name):
				with self.assertRaises(frappe.ValidationError):
					extension.extend(doc.name, extension.ADD_YEARS, years=1)
				self.assertFalse(extension.options(doc.name)["allowed"])
				frappe.db.set_value(
					DOCUMENT, doc.name, "extension_until", "2036-01-02 00:00:00", update_modified=False
				)
		pending = [doc.name for doc, _until in run.extensions({"primary", "secondary"})]
		self.assertNotIn(by_copy.name, pending)
		self.assertNotIn(by_status.name, pending)
		self.assertEqual(len(self.retention_calls()), before)
