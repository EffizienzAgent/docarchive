from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import demo
from docarchive.tests.factories import make_business_document, make_own_company, make_settings


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

	def test_mail_thread_is_stored_and_archived(self):
		seed = demo.Demo()
		seed.company = make_own_company().name
		make_settings(default_own_company=seed.company)
		seed.parties = [seed.party(name) for name in demo.PARTIES]
		seed.mail_thread()
		account = frappe.db.get_value("Email Account", {"email_id": f"info@{demo.DOMAIN}"})
		originals = frappe.get_all(
			"Mail Original",
			filters={"email_account": account},
			fields=["name", "status", "business_document"],
		)
		self.assertTrue(originals)
		self.assertTrue(all(row.status == "Stored" and row.business_document for row in originals))

	def test_administrator_sees_the_system_time_zone(self):
		frappe.db.set_value("User", "Administrator", "time_zone", "Asia/Kolkata")
		with patch("docarchive.demo.get_system_timezone", return_value="Europe/Berlin"):
			demo.Demo().user_roles()
		self.assertEqual(frappe.db.get_value("User", "Administrator", "time_zone"), "Europe/Berlin")
