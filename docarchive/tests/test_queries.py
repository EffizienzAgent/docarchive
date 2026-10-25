import frappe
from frappe.tests import IntegrationTestCase

from docarchive import queries
from docarchive.tests.factories import (
	make_business_document,
	make_document_type,
	make_outsider,
	make_own_company,
	make_party,
	make_settings,
	party_fields,
)


class TestDocumentsToAssign(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)
		self.invoice = make_document_type(number_required=1).name
		self.statement = make_document_type(counterparty_required=0).name

	def found(self):
		return {d.name: d for d in queries.documents_to_assign()}

	def test_lists_documents_without_counterparty(self):
		doc = make_business_document(document_type=self.invoice)
		self.assertEqual(self.found()[doc.name].missing, ["Counterparty"])

	def test_lists_missing_required_number(self):
		party = party_fields(make_party())
		without_number = make_business_document(document_type=self.invoice, document_number=None, **party)
		optional = make_business_document(document_type=self.statement, document_number=None, **party)
		found = self.found()
		self.assertEqual(found[without_number.name].missing, ["Document Number"])
		self.assertNotIn(optional.name, found)

	def test_lists_documents_without_file(self):
		doc = make_business_document(document_type=self.statement, file=None)
		self.assertEqual(self.found()[doc.name].missing, ["File"])

	def test_lists_failed(self):
		doc = make_business_document(document_type=self.invoice, **party_fields(make_party()))
		frappe.db.set_value(
			"Business Document", doc.name, {"archive_status": "Failed", "archive_error": "Bucket unreachable"}
		)
		found = self.found()[doc.name]
		self.assertEqual((found.archive_status, found.archive_error), ("Failed", "Bucket unreachable"))

	def test_hides_complete_and_archived(self):
		complete = make_business_document(document_type=self.invoice, **party_fields(make_party()))
		statement = make_business_document(document_type=self.statement)
		archived = make_business_document(document_type=self.invoice)
		frappe.db.set_value("Business Document", archived.name, "archive_status", "Archived")
		found = self.found()
		for doc in (complete, statement, archived):
			self.assertNotIn(doc.name, found)

	def test_newest_first(self):
		older = make_business_document(document_type=self.invoice, document_date="2026-01-10")
		newer = make_business_document(document_type=self.invoice, document_date="2026-02-10")
		names = [n for n in self.found() if n in (older.name, newer.name)]
		self.assertEqual(names, [newer.name, older.name])

	def test_count_matches_list(self):
		before = queries.count_documents_to_assign()
		make_business_document(document_type=self.invoice)
		self.assertEqual(queries.count_documents_to_assign(), before + 1)

	def test_empty_without_permission(self):
		make_business_document(document_type=self.invoice)
		with self.set_user(make_outsider("queries-outsider@example.com")):
			self.assertEqual(queries.documents_to_assign(), [])
			self.assertEqual(queries.count_documents_to_assign(), 0)

	def test_lists_correspondence_without_channel_and_direction(self):
		kind = make_document_type(is_correspondence=1, counterparty_required=0).name
		doc = make_business_document(document_type=kind)
		self.assertEqual(self.found()[doc.name].missing, ["Channel", "Direction"])
		doc.db_set({"correspondence_channel": "Fax", "correspondence_direction": "Incoming"})
		self.assertNotIn(doc.name, self.found())

	def test_correspondence_lists_only_what_is_missing(self):
		kind = make_document_type(is_correspondence=1).name
		doc = make_business_document(
			document_type=kind, correspondence_channel="Fax", correspondence_direction="Incoming"
		)
		self.assertEqual(self.found()[doc.name].missing, ["Counterparty"])
