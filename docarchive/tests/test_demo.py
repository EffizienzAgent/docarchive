from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import demo
from docarchive.tests.factories import make_business_document, make_own_company


class TestDemo(IntegrationTestCase):
	def test_refuses_without_developer_mode(self):
		with patch.dict(frappe.conf, {"developer_mode": 0}), self.assertRaises(frappe.ValidationError):
			demo.seed()

	def test_refuses_on_a_site_with_documents(self):
		make_business_document(own_company=make_own_company().name)
		with patch.dict(frappe.conf, {"developer_mode": 1}), self.assertRaises(frappe.ValidationError):
			demo.seed()

	def test_invoice_image_is_a_png(self):
		self.assertTrue(
			demo.invoice_png("Titel", "RE-1", "2026-01-01", "Kunde", 119.0).startswith(b"\x89PNG")
		)
