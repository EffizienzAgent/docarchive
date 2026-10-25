import frappe
from frappe.tests import IntegrationTestCase

from docarchive.mail import accounts
from docarchive.tests.factories import make_email_account, make_mail_settings, make_settings


class TestMailAccounts(IntegrationTestCase):
	def setUp(self):
		make_settings()
		self.account = make_email_account().name

	def test_missing_account_has_no_settings(self):
		self.assertIsNone(accounts.account_settings("nope@example.test"))
		self.assertIsNone(accounts.account_settings(None))

	def test_off_account_has_no_settings(self):
		make_mail_settings(self.account, mode="Off")
		self.assertIsNone(accounts.account_settings(self.account))

	def test_row_defaults(self):
		make_mail_settings(self.account, mode="Manual")
		row = accounts.account_settings(self.account)
		self.assertEqual((row.keep_days, row.archive_internal, row.contacts_as_counterparty), (90, 0, 0))

	def test_internal_domains_default_to_account_domain(self):
		make_mail_settings(self.account, mode="Manual")
		self.assertEqual(accounts.internal_domains(accounts.account_settings(self.account)), {"acme.test"})

	def test_internal_domains_from_row(self):
		make_mail_settings(self.account, mode="Manual", internal_domains="Acme.test, acme.de")
		row = accounts.account_settings(self.account)
		self.assertEqual(accounts.internal_domains(row), {"acme.test", "acme.de"})

	def test_duplicate_account_rows_refused(self):
		settings = frappe.get_single("Archive Settings")
		settings.set("mail_accounts", [{"email_account": self.account}, {"email_account": self.account}])
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	def test_keep_days_must_be_positive(self):
		for days in (0, -1):
			with self.assertRaises(frappe.ValidationError):
				make_mail_settings(self.account, mode="Manual", keep_days=days)
		self.assertEqual(make_mail_settings(self.account, mode="Manual", keep_days=1).keep_days, 1)

	def test_sent_folder_requires_imap(self):
		with self.assertRaises(frappe.ValidationError):
			make_mail_settings(self.account, mode="Manual", outgoing_source="Sent Folder")
		imap = make_email_account(use_imap=1).name
		row = make_mail_settings(imap, mode="Manual", outgoing_source="Sent Folder")
		self.assertEqual(row.outgoing_source, "Sent Folder")

	def test_deleting_account_keeps_its_originals(self):
		from docarchive import mail
		from docarchive.mail import archiving
		from docarchive.tests.factories import eml, make_communication, make_own_company

		make_settings(default_own_company=make_own_company().name)
		make_mail_settings(self.account, mode="Manual")
		loose = mail.capture(make_communication(self.account), eml(), "t")
		archived = mail.capture(make_communication(self.account), eml(), "t")
		archiving.archive(archived)
		settings = frappe.get_single("Archive Settings")
		settings.set("mail_accounts", [])
		settings.save(ignore_permissions=True)
		frappe.delete_doc("Email Account", self.account, ignore_permissions=True)
		for name in (loose, archived):
			self.assertEqual(frappe.db.get_value("Mail Original", name, "email_account"), self.account)
