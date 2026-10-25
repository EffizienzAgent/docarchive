import csv
import io
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import counterparty, export_package
from docarchive.tests.factories import make_party


def title(party):
	field = frappe.get_meta(party.doctype).get_title_field()
	return party.get(field) or party.name


class TestExportCounterparty(IntegrationTestCase):
	def test_meta_counterparty_gets_label_and_type(self):
		meta = {"counterparty": {"doctype": "Contact", "id": "CON-1", "name": "Acme GmbH"}}
		self.assertEqual(
			export_package.counterparty_of(frappe._dict(), meta),
			{
				"doctype": "Contact",
				"id": "CON-1",
				"name": "Acme GmbH",
				"type_label": "Contact",
				"label": "Acme GmbH (CON-1)",
			},
		)

	def test_falls_back_to_the_database(self):
		party = make_party()
		doc = frappe._dict(counterparty_type="Contact", counterparty=party.name)
		row = export_package.counterparty_of(doc, {})
		self.assertEqual((row["id"], row["name"]), (party.name, title(party)))

	def test_missing_record_shows_the_id(self):
		doc = frappe._dict(counterparty_type="Contact", counterparty="gone")
		row = export_package.counterparty_of(doc, {})
		self.assertEqual((row["name"], row["label"]), (None, "gone"))
		self.assertIsNone(export_package.counterparty_of(frappe._dict(), {}))

	def test_csv_has_the_label(self):
		row = {
			"id": "DOC-1",
			"counterparty": {"doctype": "Contact", "id": "CON-1", "name": "Acme", "label": "Acme (CON-1)"},
			"files": [{"key": "k"}],
		}
		[line] = csv.DictReader(io.StringIO(export_package.manifest_csv([row])))
		self.assertEqual(
			(
				line["counterparty_type"],
				line["counterparty"],
				line["counterparty_name"],
				line["counterparty_label"],
			),
			("Contact", "CON-1", "Acme", "Acme (CON-1)"),
		)

	def test_csv_columns_keep_the_0_8_1_order(self):
		released = (
			"id",
			"title",
			"type",
			"date",
			"number",
			"own_company",
			"counterparty_type",
			"counterparty",
			"counterparty_name",
			"lock_mode",
			"retain_until",
			"legal_hold",
			"thread_key",
			"file_role",
			"file_name",
			"key",
			"version_id",
			"sha256",
			"size",
			"verified",
		)
		self.assertEqual(export_package.CSV_FIELDS[: len(released)], released)
		self.assertEqual(export_package.CSV_FIELDS[len(released) :], ("counterparty_label",))

	def test_long_titles_are_cut(self):
		field = frappe.get_meta("Contact").get_title_field()
		row = frappe._dict({"name": "C-1", field: "x" * 300})
		with patch.object(frappe.db, "get_value", return_value=row):
			self.assertEqual(len(counterparty.title_of("Contact", "C-1")), 140)

	def test_title_of_never_raises(self):
		party = make_party()
		self.assertEqual(counterparty.title_of("Contact", party.name), title(party))
		self.assertIsNone(counterparty.title_of("Contact", "gone"))
		self.assertIsNone(counterparty.title_of("No Such Doctype", "x"))
		self.assertIsNone(counterparty.title_of(None, None))
