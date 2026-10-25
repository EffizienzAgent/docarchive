import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from docarchive.document_archive.doctype.business_document import business_document
from docarchive.patches import fill_counterparty_names
from docarchive.tests.factories import make_business_document as make_document
from docarchive.tests.factories import make_own_company, make_party, make_settings, party_fields

DOCUMENT = "Business Document"


def full_name(contact):
	return frappe.db.get_value("Contact", contact.name, "full_name")


class TestCounterpartyName(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)

	def test_filled_from_the_counterparty_title(self):
		party = make_party("Acme")
		doc = make_document(**party_fields(party))
		self.assertEqual(doc.counterparty_name, full_name(party))
		doc.counterparty_type, doc.counterparty = None, None
		doc.save(ignore_permissions=True)
		self.assertIsNone(doc.counterparty_name)

	def test_follows_a_new_counterparty_before_archiving(self):
		doc = make_document(**party_fields(make_party("Acme")))
		other = make_party("Beta")
		doc.update(party_fields(other))
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.counterparty_name, full_name(other))

	def test_frozen_once_archiving_started(self):
		party = make_party("Acme")
		doc = make_document(**party_fields(party))
		frappe.db.set_value(DOCUMENT, doc.name, "archive_started_at", now_datetime())
		before = doc.counterparty_name
		frappe.db.set_value("Contact", party.name, {"first_name": "Renamed", "full_name": "Renamed"})
		doc.reload()
		doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(DOCUMENT, doc.name, "counterparty_name"), before)

	def test_not_part_of_the_locked_fields(self):
		self.assertNotIn("counterparty_name", business_document.LOCKED_FIELDS)

	def test_backfill_only_fills_empty_names(self):
		party = make_party("Acme")
		empty = make_document(**party_fields(party))
		kept = make_document(**party_fields(party))
		frappe.db.set_value(DOCUMENT, empty.name, "counterparty_name", None)
		frappe.db.set_value(DOCUMENT, kept.name, "counterparty_name", "Kept")
		fill_counterparty_names.execute()
		self.assertEqual(frappe.db.get_value(DOCUMENT, empty.name, "counterparty_name"), full_name(party))
		self.assertEqual(frappe.db.get_value(DOCUMENT, kept.name, "counterparty_name"), "Kept")
