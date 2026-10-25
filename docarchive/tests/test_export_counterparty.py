import csv
import io

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

	def test_title_of_never_raises(self):
		party = make_party()
		self.assertEqual(counterparty.title_of("Contact", party.name), title(party))
		self.assertIsNone(counterparty.title_of("Contact", "gone"))
		self.assertIsNone(counterparty.title_of("No Such Doctype", "x"))
		self.assertIsNone(counterparty.title_of(None, None))
