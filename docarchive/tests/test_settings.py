import frappe
from frappe.tests import IntegrationTestCase

from docarchive import settings
from docarchive.tests.factories import make_document_type, make_own_company, make_settings


class TestArchiveSettings(IntegrationTestCase):
	def setUp(self):
		self.default = make_own_company("acme-gmbh").name
		self.successor = make_own_company("acme-ltd").name
		self.invoice = make_document_type(default_retention_years=None).name

	def test_defaults_disabled(self):
		doc = frappe.new_doc("Archive Settings")
		self.assertEqual(doc.enabled, 0)
		self.assertEqual(doc.min_age_hours, 24)

	def test_default_company_when_no_successor(self):
		make_settings(default_own_company=self.default)
		self.assertEqual(settings.own_company_for("2030-01-01"), self.default)

	def test_successor_from_date_switches_by_document_date(self):
		make_settings(
			default_own_company=self.default,
			successor_own_company=self.successor,
			successor_from_date="2027-01-01",
		)
		self.assertEqual(settings.own_company_for("2026-12-31"), self.default)
		self.assertEqual(settings.own_company_for("2027-01-01"), self.successor)

	def test_unset_successor_date_never_switches(self):
		make_settings(default_own_company=self.default, successor_own_company=self.successor)
		self.assertEqual(settings.own_company_for("2099-01-01"), self.default)

	def test_explicit_company_wins(self):
		make_settings(default_own_company=self.default)
		self.assertEqual(settings.own_company_for("2026-03-15", self.successor), self.successor)

	def test_disabled_explicit_company_rejected(self):
		frappe.db.set_value("Own Company", self.successor, "disabled", 1)
		make_settings(default_own_company=self.default)
		with self.assertRaises(frappe.ValidationError):
			settings.own_company_for("2026-03-15", self.successor)
		with self.assertRaises(frappe.ValidationError):
			settings.own_company_for("2026-03-15", "missing-company")

	def test_retention_years_lookup(self):
		make_settings(rules=[(self.default, self.invoice, 8)])
		self.assertEqual(settings.retention_years(self.default, self.invoice), 8)

	def test_missing_rule_returns_none(self):
		make_settings(rules=[(self.default, self.invoice, 8)])
		self.assertIsNone(settings.retention_years(self.successor, self.invoice))

	def test_type_default_when_no_rule(self):
		statement = make_document_type(default_retention_years=10).name
		make_settings(rules=[(self.default, self.invoice, 8)])
		self.assertEqual(settings.retention_years(self.default, statement), 10)
		make_settings(rules=[(self.default, statement, 12)])
		self.assertEqual(settings.retention_years(self.default, statement), 12)

	def test_duplicate_rule_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_settings(rules=[(self.default, self.invoice, 8), (self.default, self.invoice, 10)])

	def test_years_must_be_positive(self):
		for years in (0, -1):
			with self.subTest(years=years), self.assertRaises(frappe.ValidationError):
				make_settings(rules=[(self.default, self.invoice, years)])
