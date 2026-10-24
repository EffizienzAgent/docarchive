import frappe
from frappe.tests import IntegrationTestCase

from docarchive.tests.factories import make_own_company


class TestOwnCompany(IntegrationTestCase):
	def test_slug_must_be_path_safe(self):
		for slug in ("Acme GmbH", "acme/ltd", "-acme", "acme--ltd", "acme-"):
			with self.subTest(slug=slug), self.assertRaises(frappe.ValidationError):
				frappe.get_doc({"doctype": "Own Company", "slug": slug, "company_name": "Acme"}).insert()

	def test_slug_is_name(self):
		company = make_own_company("acme-ltd")
		self.assertEqual(company.name, company.slug)
		self.assertTrue(company.name.startswith("acme-ltd-"))
