import frappe
from frappe.tests import IntegrationTestCase

from docarchive.mail import suggestion
from docarchive.tests.factories import make_communication, make_email_account


class TestMailSuggestion(IntegrationTestCase):
	def tearDown(self):
		self.set_setting("suggestion_keywords", "")
		self.set_setting("archive_rule", "")

	def set_setting(self, field, value):
		frappe.db.set_single_value("Archive Settings", field, value)
		frappe.clear_document_cache("Archive Settings")

	def test_keyword_matches_at_word_start_case_insensitive(self):
		self.assertEqual(
			suggestion.reasons("WG: Auftragsbestätigung 4711", [], ["Auftrag"]), ["keyword:Auftrag"]
		)
		self.assertEqual(suggestion.reasons("Ihre rechnungen 2026", [], ["Rechnung"]), ["keyword:Rechnung"])
		self.assertEqual(suggestion.reasons("Unterauftrag", [], ["Auftrag"]), [])
		self.assertEqual(suggestion.reasons(None, [], ["Auftrag"]), [])

	def test_multi_word_keyword(self):
		self.assertEqual(suggestion.reasons("Your credit note", [], ["credit note"]), ["keyword:credit note"])

	def test_attachment_kinds_once_each(self):
		self.assertEqual(
			suggestion.reasons("Hi", ["a.PDF", "b.pdf", "x.xml", "c.png"], []),
			["attachment:pdf", "attachment:xml"],
		)

	def test_keywords_in_configured_order_before_attachments(self):
		self.assertEqual(
			suggestion.reasons("Offer and order", ["a.pdf"], ["order", "offer"]),
			["keyword:order", "keyword:offer", "attachment:pdf"],
		)

	def test_keywords_from_settings_or_default(self):
		self.set_setting("suggestion_keywords", " Offerte \n\nAuftrag ")
		self.assertEqual(suggestion.keywords(), ["Offerte", "Auftrag"])
		self.set_setting("suggestion_keywords", "")
		self.assertEqual(suggestion.keywords(), list(suggestion.DEFAULT_KEYWORDS))

	def test_rule_from_settings_or_default(self):
		self.set_setting("archive_rule", "")
		self.assertIn("When in doubt, archive.", suggestion.rule())
		self.set_setting("archive_rule", "Alles mit Kunden.")
		self.assertEqual(suggestion.rule(), "Alles mit Kunden.")

	def test_attachment_names_per_communication(self):
		account = make_email_account().name
		with_files = make_communication(account).name
		without_files = make_communication(account).name
		for file_name in ("y.png", "x.pdf"):
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": file_name,
					"content": b"data",
					"is_private": 1,
					"attached_to_doctype": "Communication",
					"attached_to_name": with_files,
				}
			).insert(ignore_permissions=True)
		self.assertEqual(
			suggestion.attachment_names([with_files, without_files]),
			{with_files: ["x.pdf", "y.png"], without_files: []},
		)
