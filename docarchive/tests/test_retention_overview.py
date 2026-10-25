from datetime import timedelta

import frappe
from frappe.utils import now_datetime

from docarchive import run, store
from docarchive.document_archive.report.retention_overview import retention_overview
from docarchive.tests.test_run import DOCUMENT, ArchiveRunTestCase


class TestRetentionOverview(ArchiveRunTestCase):
	def archived(self, **fields):
		doc = self.document(**fields)
		run.archive_document(doc.name, store.targets())
		return frappe.get_doc(DOCUMENT, doc.name)

	def names(self, **filters):
		return [row.name for row in retention_overview.execute(filters)[1]]

	def test_lists_archived_documents_with_a_chart_by_year(self):
		doc = self.archived()
		pending = self.document()
		_columns, rows, _message, chart = retention_overview.execute({})
		self.assertIn(doc.name, [row.name for row in rows])
		self.assertNotIn(pending.name, [row.name for row in rows])
		self.assertEqual(chart["data"]["labels"], ["2035"])

	def test_filters(self):
		doc = self.archived()
		self.assertEqual(self.names(document_type=self.type.name), [doc.name])
		self.assertEqual(self.names(lock_mode="GOVERNANCE"), [doc.name])
		self.assertEqual(self.names(lock_mode="COMPLIANCE"), [])
		self.assertEqual(self.names(counterparty_type="Contact", counterparty=self.party.name), [doc.name])
		self.assertEqual(self.names(ending_within_months=3), [])
		soon = now_datetime() + timedelta(days=30)
		frappe.db.set_value(DOCUMENT, doc.name, "retain_until", soon, update_modified=False)
		self.assertEqual(self.names(ending_within_months=3), [doc.name])

	def test_failed_copies(self):
		doc = self.archived()
		self.assertEqual(self.names(failed_copies=1), [])
		frappe.db.set_value("Archive Copy", doc.archive_copies[1].name, "status", "Failed")
		[row] = retention_overview.execute({"failed_copies": 1})[1]
		self.assertEqual((row.name, row.failed_copies), (doc.name, "secondary"))
