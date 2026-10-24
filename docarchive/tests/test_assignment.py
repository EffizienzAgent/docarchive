import frappe
from frappe.tests import IntegrationTestCase

from docarchive import assignment
from docarchive.tests.factories import (
	make_business_document,
	make_own_company,
	make_party,
	make_settings,
	party_fields,
)

ASSIGNED = {}


def assign_from_source(doc):
	party = ASSIGNED.get(doc.source_party_id)
	if party:
		doc.counterparty_type, doc.counterparty = party.doctype, party.name
	if ASSIGNED.get("own_company"):
		doc.own_company = ASSIGNED["own_company"]


def document(**fields):
	return frappe._dict({"own_company": None, "counterparty_type": None, "counterparty": None, **fields})


class TestAssignment(IntegrationTestCase):
	def setUp(self):
		self.default = make_own_company("acme-gmbh").name
		make_settings(default_own_company=self.default)
		self.party = make_party()
		ASSIGNED.clear()
		ASSIGNED["42"] = self.party

	def hooks(self):
		return self.patch_hooks(
			{"docarchive_assign": ["docarchive.tests.test_assignment.assign_from_source"]}
		)

	def test_without_hooks_nothing_is_assigned(self):
		doc = document(source_party_id="42")
		self.assertFalse(assignment.assign(doc))
		self.assertIsNone(doc.counterparty)

	def test_hook_assigns_counterparty(self):
		doc = document(source_party_id="42")
		with self.hooks():
			self.assertTrue(assignment.assign(doc))
		self.assertEqual((doc.counterparty_type, doc.counterparty), ("Contact", self.party.name))

	def test_existing_counterparty_not_overwritten(self):
		other = make_party("Other")
		doc = document(source_party_id="42", **party_fields(other))
		with self.hooks():
			self.assertFalse(assignment.assign(doc))
		self.assertEqual(doc.counterparty, other.name)

	def test_hook_own_company_beats_default_but_not_explicit(self):
		hooked = make_own_company("acme-ltd").name
		ASSIGNED["own_company"] = hooked
		with self.hooks():
			self.assertEqual(make_business_document().own_company, hooked)
			explicit = make_own_company("acme-explicit").name
			self.assertEqual(make_business_document(own_company=explicit).own_company, explicit)

	def test_unknown_source_stays_unassigned_on_insert(self):
		with self.hooks():
			doc = make_business_document(archive_write=True, source_party_id="unknown")
		self.assertFalse(doc.counterparty)
		self.assertEqual(doc.own_company, self.default)

	def test_assigned_on_insert(self):
		with self.hooks():
			doc = make_business_document(archive_write=True, source_system="Billing", source_party_id="42")
		self.assertEqual(doc.counterparty, self.party.name)
